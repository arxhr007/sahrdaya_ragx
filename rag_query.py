"""
rag_query.py — query routing, SQL building and formatting. No models, no chains, no index.

Split out of rag_setup.py, which builds FAISS, downloads two sentence-transformer models
and opens a Groq client at import time. That made every one of these functions untestable
and made `import rag_setup` a ~20 s operation.

Everything here depends only on the standard library, so it imports in milliseconds and is
covered directly by tests/. rag_setup.py re-exports every name, so existing callers
(main.py, api/routes/chat.py) are unaffected.
"""

import hashlib
import json
import os
import re
import sqlite3
from functools import lru_cache

URL_PATTERN = re.compile(r"https?://[^\s)\]\}>\"']+")


CREATOR_CANONICAL_LINE = (
    "This AI assistant was created by Aaron Thomas, Shayen Thomas, "
    "Mishal Shanavas, and Mathew Geejo."
)


# Signals that a question is about a PAST office-holder rather than the current one.
# "before" and "who was" matter: without them "who was the principal before" resolved to
# the sitting Principal, silently answering the opposite of what was asked.
_FORMER_INTENT_PATTERN = re.compile(
    r"\b(former|past|previous|ex|before|earlier|erstwhile|prior)\b"
    r"|\bwho\s+was\b|\bused\s+to\b",
    re.IGNORECASE,
)

CREATOR_QUERY_PATTERN = re.compile(
    r"\b(who\s+(?:created|built|made|developed)|creator|created\s+by|built\s+(?:by|this)"
    r"|made\s+by|developers?|dev\s*team|website\s*team|credits?|behind\s+this)\b",
    re.IGNORECASE,
)


GRAPH_STOPWORDS = {
    "a", "an", "and", "the", "for", "with", "from", "that", "this", "what", "when", "where",
    "which", "show", "list", "give", "need", "want", "have", "has", "all", "about", "your", "their",
    "there", "into", "data", "details", "please", "can", "you", "how", "find", "page", "pages",
}


GRAPH_ROUTE_PRINT = os.environ.get("GRAPH_ROUTE_PRINT", "1").strip().lower() in {"1", "true", "yes", "on"}


def _print_graphrag_used(question: str, num_docs: int) -> None:
    """Print a clear marker when GraphRAG route is selected (for testing)."""
    if not GRAPH_ROUTE_PRINT:
        return
    q = re.sub(r"\s+", " ", (question or "")).strip()
    if len(q) > 140:
        q = q[:137] + "..."
    print(f"[route] graph_rag used | docs={num_docs} | query=\"{q}\"")


_QUERY_EXPANSIONS = {
    r"\bcse\b":   "Computer Science Engineering CSE",
    r"\bece\b":   "Electronics and Communication Engineering ECE",
    r"\beee\b":   "Electrical and Electronics Engineering EEE",
    r"\bbme\b":   "Biomedical Engineering BME",
    r"\bbt\b":    "Biotechnology Engineering BT",
    r"\bash\b":   "Applied Science and Humanities ASH",
    r"\bce\b":    "Civil Engineering CE",
    r"\bmech\b":  "Mechanical Engineering ME",
    # Leadership names are appended at call time from the DB (see _leadership_terms)
    # rather than hardcoded here, where they went stale as staff changed.
    r"\bhods?\b":  "Head of Department HOD",
    r"\bprincipal\b": "Principal",
    r"\bexecutive director\b": "Executive Director Fr. Dr. Anto Chungath",
    r"\bchairman\b": "Chairman Mar Pauly Kannookadan Bishop",
    r"\bplacement\b": "placement training internship recruitment",
    r"\badmission\b": "admission application eligibility intake",
    r"\bformer\b": "former people previous past ex",
}


@lru_cache(maxsize=1)
def _leadership_terms() -> str:
    """Current leadership names, read from the faculty table.

    Kept out of _QUERY_EXPANSIONS so the boost terms follow the data instead of a
    hardcoded list that silently goes stale when staff change.
    """
    try:
        conn = sqlite3.connect(FACULTY_DB)
        rows = conn.execute(
            "SELECT DISTINCT name FROM faculty "
            "WHERE designation IS NOT NULL AND TRIM(designation) != '' "
            "AND designation NOT LIKE '%Professor%'"
        ).fetchall()
        conn.close()
    except Exception:
        return ""
    return " ".join(name for (name,) in rows if name)


def expand_query(question: str) -> str:
    """Expand terms for retrieval/routing; expects pre-normalized input."""
    expanded = (question or "").strip()
    if not expanded:
        return ""
    wants_leadership = bool(re.search(r"\bhods?\b|\bprincipal\b|\bdean\b|\bhead of department\b",
                                      expanded, re.IGNORECASE))
    for pattern, replacement in _QUERY_EXPANSIONS.items():
        if re.search(pattern, expanded, re.IGNORECASE):
            expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
    if wants_leadership:
        names = _leadership_terms()
        if names:
            expanded = f"{expanded} {names}"
    return expanded


_DEPARTMENT_CANONICAL_MAP = {
    r"\bcse\b|computer\s+science": "Computer Science Engineering",
    r"\bece\b|electronics\s+and\s+communication": "Electronics and Communication Engineering",
    r"\beee\b|electrical\s+and\s+electronics": "Electrical and Electronics Engineering",
    r"\bbme\b|biomedical": "Biomedical Engineering",
    r"\bbt\b|biotechnology": "Biotechnology Engineering",
    r"\bce\b|civil": "Civil Engineering",
    r"\bmech\b|mechanical": "Mechanical Engineering",
    r"\bash\b|applied\s+science": "Applied Science and Humanities",
}


# Current leadership roles, matched against the faculty.designation column.
# Ordered: "assistant hod" must win before the plain "hod" pattern.
_ROLE_CANONICAL_MAP = {
    r"\bassistant\s+hods?\b|\bassistant\s+head\s+of\s+(?:the\s+)?department\b": "Assistant HOD",
    r"\bhods?\b|\bhead\s+of\s+(?:the\s+)?department\b|\bdepartment\s+head\b": "Head of Department",
    r"\bvice\s+principal\b": "Vice Principal",
    r"\bprincipal\b": "Principal",
    r"\bdean\b": "Dean",
}


_FORMER_ROLE_CANONICAL_MAP = {
    "principal": "Principals",
    "vice principal": "Vice Principals",
    "manager": "Managers",
    "director": "Directors",
    "executive director": "Executive Directors",
    "finance officer": "Finance Officers",
    "media director": "Media Directors",
    "advisor": "Advisors",
    "chairman": "Chairmen",
    "college chairperson": "College Chairpersons",
}


def _looks_single_person_query(q: str) -> bool:
    question = (q or "").lower().strip()
    if not question:
        return False
    if re.search(r"\b(list|show all|all\s+|who are|how many|count)\b", question):
        return False
    # A named role plus a department identifies one person ("ece hod"), even without
    # a "who is" prefix. Without this the query reads as a department listing.
    if re.search(r"\bhods?\b|\bhead\s+of\s+(?:the\s+)?department\b|\bprincipal\b|\bdean\b", question):
        if not _FORMER_INTENT_PATTERN.search(question):
            return True

    patterns = [
        r"^who\s+is\s+",
        r"^tell\s+me\s+about\s+",
        r"^details\s+about\s+",
        r"^information\s+about\s+",
        r"^info\s+about\s+",
    ]
    return any(re.search(p, question) for p in patterns)


def _extract_interest_from_query(q_lower: str) -> str | None:
    patterns = [
        r"(?:students?|people)\s+(?:interested\s+in|into|who\s+like|who\s+likes)\s+([a-z0-9\-\s]{2,40})",
        r"(?:interested\s+in|into|likes?|like)\s+([a-z0-9\-\s]{2,40})\s+(?:students?|people)",
    ]
    for pat in patterns:
        m = re.search(pat, q_lower)
        if not m:
            continue
        value = re.sub(r"\s+", " ", m.group(1)).strip(" ?.!,")
        if value:
            return value
    return None


def _is_safe_mapped_query(original: str, mapped: str) -> bool:
    """Guardrail to avoid over-rewrites that change single-person intent to bulk intent."""
    o = (original or "").strip().lower()
    m = (mapped or "").strip().lower()
    if not m:
        return False

    if _looks_single_person_query(o):
        if re.search(r"\b(list|show all|all\s+|who are|how many|count)\b", m):
            return False

    return True


def _deterministic_query_map(question: str) -> str:
    """Map shorthand bulk queries to a canonical form before routing."""
    q = (question or "").strip()
    if not q:
        return ""

    q_lower = q.lower()
    # Check for list-intent keywords: explicit list verbs AND "all" when near a department.
    has_list_intent = bool(re.search(r"\b(list|show|give|members?|staff|faculty|professors?)\b", q_lower))
    has_people_entity = bool(re.search(r"\b(faculty|faculties|professor|professors|teacher|teachers|staff|hods?|members?)\b", q_lower))
    
    # Also accept "all" as list intent if accompanied by department.
    has_all_keyword = bool(re.search(r"\ball\b", q_lower))

    detected_department = None
    for pattern, canonical in _DEPARTMENT_CANONICAL_MAP.items():
        if re.search(pattern, q_lower, re.IGNORECASE):
            detected_department = canonical
            break

    # Role questions come first. "ece hod" and "who is the HOD of CSE" name ONE person,
    # but "hod" also matches has_people_entity below, which would rewrite them into a
    # whole-department listing and lose the intent entirely.
    is_former_query = bool(_FORMER_INTENT_PATTERN.search(q_lower))
    detected_role = None
    if not is_former_query:
        for pattern, canonical in _ROLE_CANONICAL_MAP.items():
            if re.search(pattern, q_lower, re.IGNORECASE):
                detected_role = canonical
                break

    if detected_role:
        if detected_department:
            return f"who is the {detected_role} of {detected_department}"
        # No department named: "list all HODs" stays bulk, "who is the principal" does not.
        if re.search(r"\b(list|show|all|every|each)\b", q_lower):
            return f"list all {detected_role}"
        return f"who is the {detected_role}"

    # Check for department + list-like pattern (e.g., "cse list all", "cse faculty", "ece members list").
    # Match if: department + list verb, OR department + people entity, OR department + "all"
    if detected_department and (has_list_intent or has_people_entity or has_all_keyword):
        return f"list all faculty in {detected_department}"

    # Former-role list intent normalization.
    for role_singular, role_plural in _FORMER_ROLE_CANONICAL_MAP.items():
        role_pattern = rf"\bformer\s+{re.escape(role_singular)}s?\b"
        if re.search(role_pattern, q_lower):
            return f"list all former {role_plural}"

    # Student-interest list normalization.
    interest = _extract_interest_from_query(q_lower)
    if interest:
        return f"list all students interested in {interest}"

    return q


CLEANED_FILE = "data/processed/data_cleaned.jsonl"


RAW_FILE     = "data/raw/sahrdaya_rag.txt"


# --- Index cache paths ---
CACHE_DIR        = ".index_cache"


FAISS_DIR        = os.path.join(CACHE_DIR, "faiss")


BM25_CACHE       = os.path.join(CACHE_DIR, "bm25.pkl")


BM25_LARGE_CACHE = os.path.join(CACHE_DIR, "bm25_large.pkl")


HASH_FILE        = os.path.join(CACHE_DIR, "data_hash.txt")


TRACKING_FILE    = "data/raw/sahrdaya_tracking.json"


GRAPH_HASH_FILE  = os.path.join(CACHE_DIR, "content_graph_hash.txt")


GRAPH_SCHEMA_V1  = "content_graph_v1"


def _data_hash() -> str:
    """Hash of data/processed/data_cleaned.jsonl to detect when data changes."""
    h = hashlib.md5()
    with open(CLEANED_FILE, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    # Also hash retrieval seeds so cache refreshes when canonical/system chunks change.
    h.update(CREATOR_CANONICAL_LINE.encode("utf-8"))
    h.update(b"creator_retrieval_v1")
    return h.hexdigest()


def _cache_is_valid() -> bool:
    """Check if cached indexes exist and match current data."""
    if not all(os.path.exists(p) for p in [FAISS_DIR, BM25_CACHE, BM25_LARGE_CACHE, HASH_FILE]):
        return False
    with open(HASH_FILE, "r") as f:
        return f.read().strip() == _data_hash()


def _save_hash():
    with open(HASH_FILE, "w") as f:
        f.write(_data_hash())


def _file_hash(path: str) -> str:
    if not os.path.exists(path):
        return "missing"
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _graph_data_hash() -> str:
    h = hashlib.md5()
    h.update(_file_hash(CLEANED_FILE).encode("utf-8"))
    h.update(_file_hash(TRACKING_FILE).encode("utf-8"))
    h.update(GRAPH_SCHEMA_V1.encode("utf-8"))
    return h.hexdigest()


def _save_graph_hash() -> None:
    with open(GRAPH_HASH_FILE, "w", encoding="utf-8") as f:
        f.write(_graph_data_hash())


def _extract_urls_from_text(text: str) -> list[str]:
    urls = []
    seen = set()
    for raw in URL_PATTERN.findall(text or ""):
        u = raw.rstrip(".,;:)")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _load_tracking_pages() -> dict:
    if not os.path.exists(TRACKING_FILE):
        return {}
    try:
        with open(TRACKING_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj.get("urls", {}) if isinstance(obj, dict) else {}
    except Exception:
        return {}


# Custom preprocessing: lowercase + split so that token matching is case-insensitive
def _bm25_preprocess(text: str) -> list[str]:
    return text.lower().split()


# Check if query is a "list all" type query
def is_list_query(question):
    q_lower = question.lower()
    list_indicators = ["list all", "list the", "show all", "show me all", "give me all",
                       "all faculty", "all faculties", "all professors", "all teachers",
                       "all hod", "all members", "all staff", "everyone in", "who are the",
                       "faculties from", "faculty from", "faculty of", "faculties of",
                       "how many faculty", "how many professors", "tell me all",
                       "members list", "member list", "list members", "list faculty"]
    return any(ind in q_lower for ind in list_indicators)


def is_creator_query(question: str) -> bool:
    """Return True for creator/credits/developer identity questions."""
    q = (question or "").strip().lower()
    if not q:
        return False
    return bool(CREATOR_QUERY_PATTERN.search(q))


def expand_creator_query(question: str) -> str:
    """Append creator-specific retrieval terms for identity/credits questions."""
    if not is_creator_query(question):
        return question
    return (
        f"{question} created by developers development team website team credits "
        "Aaron Thomas Shayen Thomas Mishal Shanavas Mathew Geejo"
    )


FACULTY_DB = "data/sql/college.db"


DB_HASH_FILE = os.path.join(CACHE_DIR, "db_source_hash.txt")


def _db_source_hash() -> str:
    """Fingerprint of the inputs the SQL DB is built from.

    FAISS/BM25 already rebuild when their source changes (_data_hash); without the
    same check here a re-scrape left college.db silently stale.
    """
    digest = hashlib.md5()
    for path in (RAW_FILE, "data/students.csv"):
        try:
            with open(path, "rb") as fh:
                digest.update(fh.read())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"|")
    return digest.hexdigest()


def _db_hash_is_current() -> bool:
    try:
        with open(DB_HASH_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip() == _db_source_hash()
    except OSError:
        return False


def _write_db_hash() -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(DB_HASH_FILE, "w", encoding="utf-8") as fh:
            fh.write(_db_source_hash())
    except OSError:
        pass


def _normalize_name_for_match(text: str) -> str:
    """Normalize person names for tolerant exact matching."""
    value = (text or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _extract_person_name_candidate(question: str) -> str | None:
    """Extract potential person name from single-person query phrasing."""
    q = (question or "").strip()
    patterns = [
        r"^\s*who\s+is\s+(.+?)\s*\??\s*$",
        r"^\s*who\s+is\s+student\s+(.+?)\s*\??\s*$",
        r"^\s*tell\s+me\s+about\s+(.+?)\s*\??\s*$",
        r"^\s*details\s+about\s+(.+?)\s*\??\s*$",
        r"^\s*info(?:rmation)?\s+about\s+(.+?)\s*\??\s*$",
    ]
    for pat in patterns:
        m = re.match(pat, q, flags=re.IGNORECASE)
        if m:
            cand = _normalize_name_for_match(m.group(1))
            return cand if cand else None
    return None


def _student_single_lookup_sql(question: str) -> str | None:
    """Return a direct SQL query for single-student lookup if name matches."""
    candidate = _extract_person_name_candidate(question)
    if not candidate:
        return None

    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("SELECT name FROM students")
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None

    matched_name = None
    for (name,) in rows:
        normalized = _normalize_name_for_match(name)
        words = normalized.split()
        # Match if candidate is exact match OR matches as a word in the name
        if candidate == normalized or candidate in words:
            matched_name = name
            break

    if not matched_name:
        return None

    # Keep this deterministic and safe: exact match on resolved canonical name.
    escaped = matched_name.replace("'", "''")
    return (
        "SELECT name, year_of_graduation, department, bio, photo_url, instagram_username, "
        "github_url, projects_links, linkedin_url, personal_website "
        f"FROM students WHERE name = '{escaped}' ORDER BY name"
    )


_ROLE_LOOKUP_COLUMNS = (
    "SELECT name, designation, department, email, experience_years, research_areas "
    "FROM faculty"
)


def _role_lookup_sql(question: str) -> str | None:
    """Deterministic SQL for current-leadership questions.

    "who is the HOD of CSE" used to reach the LLM classifier, which wrote a
    department-wide SELECT and returned all 35 CSE staff. The designation column is
    populated from the faculty API, so build the query directly and filter on it.

    Expects the canonical form produced by _deterministic_query_map(), and also
    tolerates the raw phrasing.
    """
    q = (question or "").strip().lower()
    if not q or _FORMER_INTENT_PATTERN.search(q):
        return None

    role = None
    for pattern, canonical in _ROLE_CANONICAL_MAP.items():
        if re.search(pattern, q, re.IGNORECASE):
            role = canonical
            break
    if role is None or role == "Vice Principal":
        # Vice Principal is a former-people role, not a faculty designation.
        return None

    department = None
    for pattern, canonical in _DEPARTMENT_CANONICAL_MAP.items():
        if re.search(pattern, q, re.IGNORECASE):
            department = canonical
            break

    escaped_role = role.replace("'", "''")
    where = [f"designation LIKE '%{escaped_role}%'"]
    if department:
        escaped_dept = department.replace("'", "''")
        where.append(f"department LIKE '%{escaped_dept}%'")

    return f"{_ROLE_LOOKUP_COLUMNS} WHERE {' AND '.join(where)} ORDER BY department, name"


def _is_bulk_entity_query(question: str) -> bool:
    """Gate SQL usage to explicit BULK faculty/former-people/student intents only.

    This prevents broad department questions (e.g., "what are things in CSE")
    from being routed to SQL.
    """
    q = question.lower().strip()

    # Must look like a bulk/list/filter/count intent.
    bulk_intent_patterns = [
        r"\blist\b",
        r"\bshow\b",
        r"\bgive\b",
        r"\ball\b",
        r"\bhow many\b",
        r"\bcount\b",
        r"\bwho are\b",
        r"\bwho likes\b",
        r"\bwho like\b",
        r"\blikes?\b",
        r"\bfaculty with\b",
        r"\bfaculties with\b",
        r"\bstudents?\s+with\b",
        r"\bstudents?\s+interested\s+in\b",
        r"\binterested\s+in\b",
        r"\bpeople\s+with\b",
        r"\bpeople\s+interested\s+in\b",
        r"\bwho\s+interested\s+in\b",
        r"\bgraduating\b",
        r"\bgraduates\b",
        r"\bformer\b",
        r"\bpast\b",
        r"\bprevious\b",
    ]
    has_bulk_intent = any(re.search(p, q) for p in bulk_intent_patterns)
    if not has_bulk_intent:
        return False

    # Must explicitly target faculty/staff OR former people concepts.
    target_entity_patterns = [
        r"\bfaculty\b",
        r"\bfaculties\b",
        r"\bprofessor\b",
        r"\bprofessors\b",
        r"\bteacher\b",
        r"\bteachers\b",
        r"\bstaff\b",
        r"\bhods\b",
        r"\bstudent\b",
        r"\bstudents\b",
        r"\bpeople\b",
        r"\bperson\b",
        r"\binterest\b",
        r"\binterests\b",
        r"\bgraduation\b",
        r"\bgraduating\b",
        r"\bformer\b",
        r"\bpast\b",
        r"\bprevious\b",
        r"\bformer people\b",
        r"\bformer principals\b",
        r"\bformer vice principals\b",
        r"\bformer managers\b",
        r"\bformer directors\b",
    ]
    has_target_entity = any(re.search(p, q) for p in target_entity_patterns)
    return has_target_entity


def _get_faculty_columns() -> set[str]:
    """Return the set of column names in the faculty table."""
    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(faculty)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        return cols
    except Exception:
        return set()


def _get_former_columns() -> set[str]:
    """Return the set of column names in the former_people table."""
    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(former_people)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        return cols
    except Exception:
        return set()


def _get_students_columns() -> set[str]:
    """Return the set of column names in the students table."""
    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(students)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        return cols
    except Exception:
        return set()


def _get_interests_columns() -> set[str]:
    """Return the set of column names in the interests table."""
    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(interests)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        return cols
    except Exception:
        return set()


def _get_student_interests_columns() -> set[str]:
    """Return the set of column names in the student_interests table."""
    try:
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(student_interests)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        return cols
    except Exception:
        return set()


def validate_faculty_sql(sql: str) -> bool:
    """Check that all columns referenced in the SQL actually exist in the target table(s).
    Returns True if valid, False if any referenced column is not in the schema."""
    # Determine which table(s) are queried
    sql_upper = sql.upper()
    valid_cols = set()
    if 'FORMER_PEOPLE' in sql_upper:
        valid_cols |= _get_former_columns()
    if 'STUDENTS' in sql_upper:
        valid_cols |= _get_students_columns()
    if 'INTERESTS' in sql_upper:
        valid_cols |= _get_interests_columns()
    if 'STUDENT_INTERESTS' in sql_upper:
        valid_cols |= _get_student_interests_columns()
    if 'FACULTY' in sql_upper:
        valid_cols |= _get_faculty_columns()
    if not any(t in sql_upper for t in ['FACULTY', 'FORMER_PEOPLE', 'STUDENTS', 'INTERESTS', 'STUDENT_INTERESTS']):
        valid_cols |= _get_faculty_columns()
    if not valid_cols:
        return False

    # Remove string literals so their content doesn't confuse the parser
    cleaned = re.sub(r"'[^']*'", "''", sql)

    # Tokenise: grab word-like identifiers (skip SQL keywords, functions, etc.)
    sql_keywords = {
        'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN',
        'IS', 'NULL', 'ORDER', 'BY', 'GROUP', 'HAVING', 'AS', 'ON', 'JOIN',
        'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'DISTINCT', 'ALL', 'ASC',
        'DESC', 'LIMIT', 'OFFSET', 'UNION', 'EXCEPT', 'INTERSECT', 'EXISTS',
        'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'CAST', 'COUNT', 'SUM', 'AVG',
        'MIN', 'MAX', 'UPPER', 'LOWER', 'LENGTH', 'SUBSTR', 'TRIM', 'REPLACE',
        'COALESCE', 'IFNULL', 'NULLIF', 'TYPEOF', 'TOTAL', 'ABS', 'ROUND',
        'INTEGER', 'TEXT', 'REAL', 'BLOB', 'PRIMARY', 'KEY', 'AUTOINCREMENT',
        'TABLE', 'FACULTY', 'FORMER_PEOPLE', 'STUDENTS', 'INTERESTS', 'STUDENT_INTERESTS', 'TRUE', 'FALSE',
    }
    tokens = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', cleaned)
    for tok in tokens:
        if tok.upper() in sql_keywords:
            continue
        # If it looks like a column name (lowercase with underscores) and isn't valid
        if tok.lower() == tok and '_' in tok and tok not in valid_cols:
            return False
    return True


def execute_faculty_sql(sql: str) -> tuple[list[str], list[tuple]] | None:
    """Execute a read-only SQL query on data/sql/college.db. Returns (column_names, rows) or None on error."""
    # Safety: only allow SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return None
    try:
        # Open read-only. The SELECT check plus sqlite3's one-statement-per-execute
        # rule already block the obvious attacks, but this query text is written by an
        # LLM, so remove write capability entirely rather than rely on that.
        conn = sqlite3.connect(
            "file:%s?mode=ro" % FACULTY_DB.replace("?", "%3f").replace("#", "%23"),
            uri=True,
        )
        cur = conn.cursor()
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
        conn.close()
        return columns, rows
    except Exception as e:
        print(f"  [SQL Error] {e}")
        return None


def _drop_empty_columns(columns: list[str], rows: list[tuple]) -> tuple[list[str], list[tuple]]:
    """Remove columns where every row is NULL/blank/zero — they only add width."""
    keep = []
    for i, name in enumerate(columns):
        for row in rows:
            value = row[i]
            if value is None:
                continue
            if isinstance(value, (int, float)) and value == 0:
                continue
            if str(value).strip():
                keep.append(i)
                break
    if not keep or len(keep) == len(columns):
        return columns, rows
    return [columns[i] for i in keep], [tuple(row[i] for i in keep) for row in rows]


# The columns worth showing when listing people; anything else is detail for a single
# profile, not for a roster.
_PEOPLE_SUMMARY_COLUMNS = (
    "name", "designation", "role", "department", "email",
    "experience_years", "start_year", "end_year", "year_of_graduation",
)


def _limit_people_columns(columns: list[str], rows: list[tuple]) -> tuple[list[str], list[tuple]]:
    """For multi-row people results, keep a readable summary column set."""
    lowered = [c.lower() for c in columns]
    if "name" not in lowered:
        return columns, rows
    keep = [i for i, c in enumerate(lowered) if c in _PEOPLE_SUMMARY_COLUMNS]
    if not keep or len(keep) == len(columns):
        return columns, rows
    return [columns[i] for i in keep], [tuple(row[i] for i in keep) for row in rows]


def format_sql_results(columns: list[str], rows: list[tuple], question: str = "") -> str:
    """Format SQL query results as a markdown table."""
    if not rows:
        return "No matching records found."

    # Single aggregate result (e.g. COUNT(*))
    if len(columns) == 1 and len(rows) == 1 and isinstance(rows[0][0], (int, float)):
        col = columns[0].replace("_", " ").title()
        return f"**{col}: {rows[0][0]}**"

    # Student single-profile output is easier to read as labeled fields.
    student_profile_cols = {
        "name",
        "year_of_graduation",
        "department",
        "bio",
        "photo_url",
        "instagram_username",
        "github_url",
        "projects_links",
        "linkedin_url",
        "personal_website",
    }
    if len(rows) == 1 and student_profile_cols.issuperset({c.lower() for c in columns}):
        label_map = {
            "name": "Name",
            "year_of_graduation": "Graduation Year",
            "department": "Department",
            "bio": "Bio",
            "photo_url": "Photo",
            "instagram_username": "Instagram",
            "github_url": "GitHub",
            "projects_links": "Projects",
            "linkedin_url": "LinkedIn",
            "personal_website": "Website",
        }
        pairs = []
        row = rows[0]
        for i, value in enumerate(row):
            if value is None or str(value).strip() == "":
                continue
            key = columns[i].lower()
            label = label_map.get(key, columns[i].replace("_", " ").title())
            pairs.append(f"- **{label}:** {value}")
        return "\n".join(pairs)

    # Table output.
    # `SELECT *` on faculty yields 18 columns, most of them empty for most people —
    # "list all faculty" rendered a 21 KB wall of blank cells. Narrow the table to the
    # columns that actually carry information before formatting.
    columns, rows = _drop_empty_columns(columns, rows)
    if len(rows) > 1:
        columns, rows = _limit_people_columns(columns, rows)

    # Clean column names for display
    display_cols = []
    for c in columns:
        c = c.replace("_", " ").title()
        c = c.replace("Has Phd", "PhD").replace("Phd Pursuing", "PhD Pursuing")
        c = c.replace("Experience Years", "Experience (Yrs)")
        c = c.replace("Start Year", "From").replace("End Year", "To")
        display_cols.append(c)

    header = "| # | " + " | ".join(display_cols) + " |"
    separator = "|---" * (len(display_cols) + 1) + "|"

    lines = [header, separator]
    for i, row in enumerate(rows, 1):
        cells = []
        for j, val in enumerate(row):
            col_lower = columns[j].lower()
            if col_lower in ('has_phd', 'phd_pursuing'):
                cells.append('Yes' if val else 'No')
            elif val is None:
                cells.append('')
            else:
                cells.append(str(val))
        lines.append(f"| {i} | " + " | ".join(cells) + " |")

    lines.append(f"\n**Total: {len(rows)} result(s)**")
    return "\n".join(lines)


def _tokenize_graph_query(question: str) -> list[str]:
    q = (question or "").lower()
    tokens = re.findall(r"[a-z0-9]{2,}", q)
    return [t for t in tokens if t not in GRAPH_STOPWORDS]


def _graph_link_intent(question: str) -> bool:
    q = (question or "").lower()
    keys = ["link", "url", "download", "pdf", "document", "source"]
    return any(k in q for k in keys)


def _extract_urls_from_docs(doc_list, limit=8):
    """Extract unique URLs from document content and metadata values."""
    out = []
    seen = set()

    def _is_static_asset(u):
        low = u.lower().split("?")[0]
        return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".css", ".js"))

    def _priority(u):
        low = u.lower()
        if ".pdf" in low or "alt=media" in low:
            return 0
        if any(ext in low for ext in [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"]):
            return 1
        return 2

    candidates = []

    def _add_url(url):
        u = (url or "").rstrip(".,;:)")
        if u and u not in seen and not _is_static_asset(u):
            seen.add(u)
            candidates.append(u)

    for doc in doc_list:
        for match in URL_PATTERN.findall(doc.page_content or ""):
            _add_url(match)

        md = doc.metadata or {}
        for _, value in md.items():
            if isinstance(value, str):
                for match in URL_PATTERN.findall(value):
                    _add_url(match)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for match in URL_PATTERN.findall(item):
                            _add_url(match)

    for u in sorted(candidates, key=_priority):
        out.append(u)
        if len(out) >= limit:
            break
    return out
