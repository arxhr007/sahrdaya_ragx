from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers.ensemble import EnsembleRetriever
from langchain_groq import ChatGroq
from langchain_core.runnables import RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from operator import itemgetter
from functools import lru_cache
import json
import os
import re
from dotenv import load_dotenv
load_dotenv()
import hashlib
import pickle
import time
import sqlite3
from sentence_transformers import CrossEncoder

from sql_extractors.student_db import ensure_student_data


URL_PATTERN = re.compile(r"https?://[^\s)\]\}>\"']+")

CREATOR_CANONICAL_LINE = (
    "This AI assistant was created by Aaron Thomas, Shayen Thomas, "
    "Mishal Shanavas, and Mathew Geejo."
)

CREATOR_QUERY_PATTERN = re.compile(
    r"\b(who\s+created\s+you|creator|created\s+by|built\s+by|developers?|dev\s*team|website\s*team|credits?)\b",
    re.IGNORECASE,
)

CREATOR_BOOST_TERMS = [
    "created by",
    "creator",
    "developers",
    "developer",
    "website team",
    "development team",
    "backend & automation developer",
    "infrastructure",
    "devops",
    "frontend",
    "aaron thomas",
    "shayen thomas",
    "mishal shanavas",
    "mathew geejo",
]

# ============ QUERY EXPANSION ============

_query_correct_chain = None
_query_map_chain = None


def normalize_user_query(question: str) -> str:
    """Apply LLM-based query correction before routing/retrieval."""
    normalized = (question or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _llm_correct_query(normalized)


@lru_cache(maxsize=512)
def _llm_correct_query(query: str) -> str:
    """Use the configured LLM to correct spelling/typos while preserving intent."""
    if not query:
        return query
    if _query_correct_chain is None:
        return query
    try:
        corrected = _query_correct_chain.invoke({"question": query}).strip()
        corrected = corrected.strip("`\"'")
        corrected = re.sub(r"\s+", " ", corrected).strip()
        return corrected or query
    except Exception:
        return query

_QUERY_EXPANSIONS = {
    r"\bcse\b":   "Computer Science Engineering CSE",
    r"\bece\b":   "Electronics and Communication Engineering ECE",
    r"\beee\b":   "Electrical and Electronics Engineering EEE",
    r"\bbme\b":   "Biomedical Engineering BME",
    r"\bbt\b":    "Biotechnology Engineering BT",
    r"\bash\b":   "Applied Science and Humanities ASH",
    r"\bce\b":    "Civil Engineering CE",
    r"\bmech\b":  "Mechanical Engineering ME",
    r"\bhods?\b":  "Head of Department HOD Manishankar Drisya Dhanya Vijikala Sukhila Jis Paul Ambily Francis",
    r"\bprincipal\b": "Principal Dr. Ramkumar",
    r"\bexecutive director\b": "Executive Director Fr. Dr. Anto Chungath",
    r"\bchairman\b": "Chairman Mar Pauly Kannookadan Bishop",
    r"\bplacement\b": "placement training internship recruitment",
    r"\badmission\b": "admission application eligibility intake",
    r"\bformer\b": "former people previous past ex",
}

def expand_query(question: str) -> str:
    """Expand abbreviated terms in the query for better embedding match."""
    normalized_question = normalize_user_query(question)
    expanded = normalized_question
    for pattern, replacement in _QUERY_EXPANSIONS.items():
        if re.search(pattern, normalized_question, re.IGNORECASE):
            expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
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


def _deterministic_query_map(question: str) -> str:
    """Map shorthand bulk queries to a canonical form before routing."""
    q = (question or "").strip()
    if not q:
        return ""

    q_lower = q.lower()
    has_list_intent = bool(re.search(r"\b(list|show|give|who\s+are|all|members?)\b", q_lower))
    has_people_entity = bool(re.search(r"\b(faculty|faculties|professor|professors|teacher|teachers|staff|hods?|members?)\b", q_lower))

    detected_department = None
    for pattern, canonical in _DEPARTMENT_CANONICAL_MAP.items():
        if re.search(pattern, q_lower, re.IGNORECASE):
            detected_department = canonical
            break

    # Example: "cse members list" -> "list all faculty in Computer Science Engineering"
    if detected_department and has_list_intent and has_people_entity:
        return f"list all faculty in {detected_department}"

    return q


def map_query_to_preset(question: str) -> str:
    """Canonicalize natural-language query shape for reliable SQL/RAG routing."""
    baseline = _deterministic_query_map(question)
    if not baseline:
        return ""
    if _query_map_chain is None:
        return baseline

    try:
        mapped = _query_map_chain.invoke({"question": baseline}).strip()
        mapped = mapped.strip("`\"'")
        mapped = re.sub(r"\s+", " ", mapped).strip()
        return mapped or baseline
    except Exception:
        return baseline


# ============ LOAD PRE-PROCESSED DATA ============
# Expects data/processed/data_cleaned.jsonl produced by python preprocess_data.py

CLEANED_FILE = "data/processed/data_cleaned.jsonl"
RAW_FILE     = "data/raw/sahrdaya_rag.txt"

# Keep raw text for direct faculty extraction
raw_docs_text = ""
if os.path.exists(RAW_FILE):
    with open(RAW_FILE, "r", encoding="utf-8") as f:
        raw_docs_text = f.read()

if os.path.exists(CLEANED_FILE):
    print(f"[*] Loading pre-processed chunks from {CLEANED_FILE}...")
    docs = []
    with open(CLEANED_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            docs.append(Document(
                page_content=obj["content"],
                metadata={
                    "chunk_id": obj.get("parent_chunk", obj["id"]),
                    "sub_chunk": obj["id"],
                    "categories": ",".join(obj.get("categories", ["general"])),
                },
            ))
    print(f"[*] Loaded {len(docs)} optimized chunks (already cleaned + re-chunked)")
else:
    print(f"[!] {CLEANED_FILE} not found — run:  python preprocess_data.py")
    print("[*] Falling back to raw data/raw/sahrdaya_rag.txt loading...")
    from langchain_community.document_loaders import DirectoryLoader, TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    loader = DirectoryLoader(".", glob="*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
    documents = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
    docs = text_splitter.split_documents(documents)
    print(f"[*] Created {len(docs)} chunks (unoptimized — run preprocess_data.py for better results)")

# Add a canonical attribution chunk so creator/credits questions always have a stable source.
docs.append(
    Document(
        page_content=CREATOR_CANONICAL_LINE,
        metadata={
            "chunk_id": "canonical_creators",
            "sub_chunk": "canonical_creators",
            "categories": "about,developers,team,credits",
            "source": "system_canonical",
        },
    )
)

# ============ RETRIEVERS ============

# Embeddings (local, no API key needed)
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# --- Index cache paths ---
CACHE_DIR        = ".index_cache"
FAISS_DIR        = os.path.join(CACHE_DIR, "faiss")
BM25_CACHE       = os.path.join(CACHE_DIR, "bm25.pkl")
BM25_LARGE_CACHE = os.path.join(CACHE_DIR, "bm25_large.pkl")
HASH_FILE        = os.path.join(CACHE_DIR, "data_hash.txt")

os.makedirs(CACHE_DIR, exist_ok=True)

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

# Custom preprocessing: lowercase + split so that token matching is case-insensitive
def _bm25_preprocess(text: str) -> list[str]:
    return text.lower().split()

_t0 = time.time()

if _cache_is_valid():
    # --- Load from cache ---
    print("[*] Loading cached FAISS index...")
    vectorstore = FAISS.load_local(FAISS_DIR, embeddings, allow_dangerous_deserialization=True)
    print(f"[*] FAISS vector index loaded from cache")

    print("[*] Loading cached BM25 indexes...")
    with open(BM25_CACHE, "rb") as f:
        bm25_retriever = pickle.load(f)
    with open(BM25_LARGE_CACHE, "rb") as f:
        bm25_retriever_large = pickle.load(f)
    print(f"[*] BM25 lexical indexes loaded from cache")
else:
    # --- Build from scratch and save ---
    print("[*] Building FAISS vector index (first run or data changed)...")
    vectorstore = FAISS.from_documents(docs, embeddings)
    vectorstore.save_local(FAISS_DIR)
    print(f"[*] FAISS vector index built & cached")

    print("[*] Building BM25 lexical indexes...")
    bm25_retriever = BM25Retriever.from_documents(docs, k=8, preprocess_func=_bm25_preprocess)
    bm25_retriever_large = BM25Retriever.from_documents(docs, k=50, preprocess_func=_bm25_preprocess)
    with open(BM25_CACHE, "wb") as f:
        pickle.dump(bm25_retriever, f)
    with open(BM25_LARGE_CACHE, "wb") as f:
        pickle.dump(bm25_retriever_large, f)
    print(f"[*] BM25 lexical indexes built & cached")

    _save_hash()

_t1 = time.time()
print(f"[*] Indexes ready in {_t1 - _t0:.1f}s")

# Vector retrievers with MMR for diversity
vector_retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 8, "fetch_k": 25, "lambda_mult": 0.7},
)
vector_retriever_large = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 30, "fetch_k": 60, "lambda_mult": 0.5},
)

# Hybrid retrievers: BM25 (keyword) + Vector (semantic), weighted
# BM25 gets higher weight (0.6) — better for exact names, roles, keywords
# Vector gets 0.4 — covers semantic similarity and paraphrased queries
retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, vector_retriever],
    weights=[0.6, 0.4],
)
retriever_large = EnsembleRetriever(
    retrievers=[bm25_retriever_large, vector_retriever_large],
    weights=[0.6, 0.4],
)
print(f"[*] Hybrid retrievers ready (BM25 + Vector)")

# ============ CROSS-ENCODER RERANKER ============
# After hybrid retrieval returns candidates, the cross-encoder scores each
# (query, document) pair jointly — much more accurate than bi-encoder similarity.
# Model: ms-marco-MiniLM-L-6-v2 (~22 MB, runs locally, no API key needed)

print("[*] Loading cross-encoder reranker...")
_reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
print("[*] Cross-encoder reranker ready")


def rerank_docs(query: str, docs: list, top_k: int) -> list:
    """Rerank retrieved documents using the cross-encoder.
    
    Scores each (query, doc) pair jointly and returns the top_k documents
    sorted by cross-encoder score (highest first).
    """
    if not docs:
        return docs
    # Score each (query, document) pair
    pairs = [[query, doc.page_content] for doc in docs]
    scores = _reranker.predict(pairs)
    # Attach scores and sort descending
    scored = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]

# LLM
# ------------------ GROQ API KEY (from environment variable) ------------------
# Support both a single key and a comma-separated key pool.
# For bootstrap objects in this module, pick the first available key.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    _pool_raw = os.environ.get("GROQ_API_KEYS", "").strip()
    if _pool_raw:
        _pool = [k.strip() for k in _pool_raw.split(",") if k.strip()]
        if _pool:
            GROQ_API_KEY = _pool[0]

if not GROQ_API_KEY:
    print("\n" + "="*70)
    print("ERROR: Groq API Key is missing!")
    print("="*70)
    print("\nPlease get your API key from:")
    print("🔗 https://console.groq.com/keys")
    print("\nThen set it as an environment variable:")
    print('   Windows (PowerShell): $env:GROQ_API_KEY = "gsk_..."')
    print('   Or set GROQ_API_KEYS="gsk_1,gsk_2,..." in .env')
    print('   Linux/macOS:          export GROQ_API_KEY="gsk_..."')
    print("="*70 + "\n")
    exit(1)

llm = ChatGroq(
    groq_api_key=GROQ_API_KEY,
    model_name="openai/gpt-oss-120b"
)

_QUERY_CORRECT_PROMPT = ChatPromptTemplate.from_template("""You are a query typo corrector for a college chatbot.

Task:
- Correct spelling mistakes and minor grammar in the user query.
- Preserve original meaning and intent exactly.
- Preserve entities/acronyms like CSE, ECE, BME, HOD, SCET.
- Do not add new facts.
- Do not answer the query.

Return only the corrected query text, nothing else.

User query: {question}
""")

_query_correct_chain = _QUERY_CORRECT_PROMPT | llm | StrOutputParser()

_QUERY_MAP_PROMPT = ChatPromptTemplate.from_template("""You rewrite college chatbot questions into canonical routing-friendly wording.

Goal:
- Keep the original meaning intact.
- Rewrite shorthand/fragment queries into a clear full query.
- Prefer canonical forms for list intents so routing is stable.

Canonical examples:
- "cse members list" -> "list all faculty in Computer Science Engineering"
- "ece faculty" -> "list all faculty in Electronics and Communication Engineering"
- "students into chess" -> "list all students interested in chess"
- "former principals" -> "list all former Principals"

Rules:
- Do not answer the question.
- Do not add facts.
- Return only one rewritten query line.

User query: {question}
""")

_query_map_chain = _QUERY_MAP_PROMPT | llm | StrOutputParser()

# Max characters for context (to stay under token limit ~6000 tokens = ~24000 chars)
MAX_CONTEXT_CHARS = 22000

# Helper function to format documents with size limit
def format_docs(docs, max_chars=MAX_CONTEXT_CHARS):
    result = []
    total_chars = 0
    for doc in docs:
        content = doc.page_content
        if total_chars + len(content) + 10 > max_chars:
            remaining = max_chars - total_chars - 10
            if remaining > 200:
                result.append(content[:remaining] + "...")
            break
        result.append(content)
        total_chars += len(content) + 10
    return "\n\n---\n\n".join(result)

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


def rerank_docs_with_creator_boost(query: str, docs: list, top_k: int, creator_intent: bool) -> list:
    """Rerank with cross-encoder and apply a light lexical boost for creator intents."""
    if not docs:
        return docs

    pairs = [[query, doc.page_content] for doc in docs]
    scores = _reranker.predict(pairs)

    scored = []
    for score, doc in zip(scores, docs):
        boosted = float(score)
        if creator_intent:
            content = (doc.page_content or "").lower()
            cats = str((doc.metadata or {}).get("categories", "")).lower()
            hit_count = sum(1 for term in CREATOR_BOOST_TERMS if term in content)
            if any(term in cats for term in ["developer", "team", "about", "credits"]):
                hit_count += 2
            if str((doc.metadata or {}).get("sub_chunk", "")).lower() == "canonical_creators":
                hit_count += 4
            boosted += 0.12 * hit_count
        scored.append((boosted, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]

# Prompt template
prompt = ChatPromptTemplate.from_template("""You are the official AI assistant for Sahrdaya College of Engineering & Technology (SCET), Kodakara, Thrissur, Kerala.

CONVERSATION HISTORY:
{chat_history}

CONTEXT:
{context}

QUESTION: {question}

INSTRUCTIONS:
- Answer strictly from the context. Include names, roles, dates, numbers when available.
- For people: provide Name, Designation, Department, Email if present.
- For LIST queries: show ALL matching items in a numbered list or table.
- Resolve pronouns using conversation history.
- If the answer is not in context, suggest visiting: https://sahrdaya.ac.in/.
- Redirect non-college queries to Sahrdaya topics.
- If asked for a document, PDF, regulation, form, handbook, download, placements report, or statistics file, return direct URL(s) from context first.
- Always print raw URLs in plain text (starting with http:// or https://). Do not hide links behind markdown labels.
- Never invent URLs. If none are present in context, explicitly say no direct link was found in context.
- Be concise but complete.""")

# ============ FACULTY SQL DATABASE ============

FACULTY_DB = "data/sql/college.db"

# Build DB on first import if it doesn't exist
if not os.path.exists(FACULTY_DB):
    print("[*] data/sql/college.db not found — building from data/raw/sahrdaya_rag.txt...")
    from sql_db_setup import build_db
    build_db(FACULTY_DB)
else:
    _conn = sqlite3.connect(FACULTY_DB)
    _cnt = _conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    # Ensure former_people table exists (may need rebuild if DB predates this table)
    _has_former = _conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='former_people'"
    ).fetchone()[0]
    if not _has_former:
        _conn.close()
        print("[*] former_people table missing — rebuilding data/sql/college.db...")
        os.remove(FACULTY_DB)
        from sql_db_setup import build_db
        build_db(FACULTY_DB)
        _conn = sqlite3.connect(FACULTY_DB)
        _cnt = _conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
        _fcnt = _conn.execute("SELECT COUNT(*) FROM former_people").fetchone()[0]
        _conn.close()
        print(f"[*] Faculty SQL database rebuilt ({_cnt} faculty + {_fcnt} former people)")
    else:
        _fcnt = _conn.execute("SELECT COUNT(*) FROM former_people").fetchone()[0]
        _conn.close()
        print(f"[*] Faculty SQL database loaded ({_cnt} faculty + {_fcnt} former people)")

# Ensure student tables/data exist in the same shared DB file.
_student_stats = ensure_student_data(FACULTY_DB)
_conn = sqlite3.connect(FACULTY_DB)
_scnt = _conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
_icnt = _conn.execute("SELECT COUNT(*) FROM interests").fetchone()[0]
_sicnt = _conn.execute("SELECT COUNT(*) FROM student_interests").fetchone()[0]
_conn.close()
if _student_stats.get("csv_found"):
    print(f"[*] Student data loaded ({_scnt} students, {_icnt} canonical interests, {_sicnt} links)")
else:
    print("[*] data/students.csv not found — student tables ready (0 rows loaded)")

# ── Schema description for LLM ──────────────────────────────────────────────────

_FACULTY_SCHEMA = """
TABLE 1: faculty  (current faculty & staff)
COLUMNS:
  id               INTEGER PRIMARY KEY
  name             TEXT        -- faculty member's full name
  designation      TEXT        -- e.g. 'Assistant Professor', 'Associate Professor', 'Professor', 'Head Of Department', 'Assistant Head Of Department', 'Principal', 'Dean'
  department       TEXT        -- one of: 'Computer Science Engineering', 'Electronics and Communication Engineering', 'Electrical and Electronics Engineering', 'Civil Engineering', 'Biotechnology Engineering', 'Biomedical Engineering', 'Applied Science and Humanities'
  email            TEXT        -- @sahrdaya.ac.in email
  has_phd          INTEGER     -- 1 if holds PhD, 0 otherwise
  phd_pursuing     INTEGER     -- 1 if currently pursuing PhD, 0 otherwise
  experience_years REAL        -- years of experience
  publications     INTEGER     -- number of publications
  research         INTEGER     -- number of research projects
  awards           INTEGER     -- number of awards
  patents          INTEGER     -- number of patents
  books            INTEGER     -- number of books authored
  joined           TEXT        -- date joined (YYYY-MM-DD)
  research_areas   TEXT        -- comma-separated research interests
  education        TEXT        -- education history text
  memberships      TEXT        -- professional memberships

TABLE 2: former_people  (past office-bearers who are no longer serving)
COLUMNS:
  id         INTEGER PRIMARY KEY
  name       TEXT        -- person's full name
  role       TEXT        -- one of: 'Chairman', 'Manager', 'Executive Director', 'Finance Officer', 'Advisor', 'Director', 'Principal', 'Vice Principal', 'Media Director', 'College Chairpersons'
  start_year INTEGER     -- year they started the role
  end_year   INTEGER     -- year they ended the role

TABLE 3: students  (student profiles from data/students.csv)
COLUMNS:
    id                 INTEGER PRIMARY KEY
    timestamp          TEXT        -- form submission timestamp
    name               TEXT        -- student's full name
    year_of_graduation INTEGER     -- graduation year (e.g., 2027)
    department         TEXT        -- normalized department name
    bio                TEXT        -- short bio
    photo_url          TEXT        -- optional photo URL (often empty in newer CSV format)
    instagram_username TEXT        -- instagram username
    github_url         TEXT        -- github URL
    projects_links     TEXT        -- comma-separated project links
    linkedin_url       TEXT        -- linkedin URL
    personal_website   TEXT        -- personal website URL

TABLE 4: interests  (canonical interests dictionary)
COLUMNS:
    id             INTEGER PRIMARY KEY
    canonical_name TEXT        -- standardized token (e.g., 'chess', 'machine learning')

TABLE 5: student_interests  (many-to-many student-interest links)
COLUMNS:
    student_id     INTEGER
    interest_id    INTEGER

DEPARTMENT ALIASES (for faculty table):
  cse, cs       -> 'Computer Science Engineering'
  ece           -> 'Electronics and Communication Engineering'
  eee           -> 'Electrical and Electronics Engineering'
  civil, ce     -> 'Civil Engineering'
  biotech, bt   -> 'Biotechnology Engineering'
  bme, biomed   -> 'Biomedical Engineering'
  ash           -> 'Applied Science and Humanities'
  mech, me      -> (no Mechanical dept in data currently)

IMPORTANT NOTES:
  - For faculty table: use LIKE with %keyword% for department matching
  - For PhD queries: use has_phd = 1 for completed, phd_pursuing = 1 for pursuing
  - For HODs: WHERE designation LIKE '%Head%Department%'
  - For FORMER/PAST people: query the former_people table, use exact role match (role = 'Principal'), NOT LIKE
    - For student-interest queries, use JOINs across students + student_interests + interests
    - Interest matching should use canonical_name and exact equality where possible
        Example: WHERE interests.canonical_name = 'chess'
    - Department + interest combined example:
        SELECT students.name, students.department
        FROM students
        JOIN student_interests ON student_interests.student_id = students.id
        JOIN interests ON interests.id = student_interests.interest_id
        WHERE students.department LIKE '%Computer Science Engineering%'
            AND interests.canonical_name = 'chess'
        ORDER BY students.name
  - A question about "former Principals" → SELECT * FROM former_people WHERE role = 'Principal'
  - A question about "former Vice Principals" → SELECT * FROM former_people WHERE role = 'Vice Principal'
  - A question about "all former people" → SELECT * FROM former_people ORDER BY role, start_year
    - Always ORDER BY students.name (students), name (faculty), or role, start_year (former_people) for consistent output
  - Use COUNT(*) for "how many" questions
  - Keep queries SELECT-only (read-only)
    - NEVER query the faculty table for former/past/previous people — use former_people
"""

# ── SQL classification + generation prompt ───────────────────────────────────────

_SQL_CLASSIFY_PROMPT = ChatPromptTemplate.from_template(
"""You are a query classifier for a college database.

Given a user question, decide if it should be answered by querying the SQL database.

The database contains faculty/staff info, former people history, and student profile+interest data.

IMPORTANT — Use SQL ONLY for BULK/LIST queries that need to retrieve or filter MULTIPLE faculty members.

Generate SQL for these types of queries:
- "list all CSE faculty" / "faculty of ECE" / "show all professors" → SELECT from faculty table
- "CSE faculty with PhD" / "faculty with more than 5 publications" → filtered lists from faculty
- "how many faculty have PhD" / "count of ECE professors" → aggregate counts from faculty
- "faculty pursuing PhD in CSE" → filtered lists from faculty
- "list all former Principals" / "previous Managers" / "past Directors" → SELECT from former_people table
- "who were the former Vice Principals" / "all former people" → SELECT from former_people table
- "list all students" / "students in CSE" / "students graduating in 2027" → SELECT from students table
- "students interested in chess" / "students interested in machine learning" → JOIN students + student_interests + interests
- ANY query asking for a LIST, ALL, COUNT, or FILTERED SET of faculty, former people, OR students

Respond NOT_SQL for these (let the RAG chatbot handle them naturally):
- "who is the HOD of CSE" / "who is the principal" → asking about ONE specific person
- "tell me about Dr. Raju G" / "who is minnuja" → individual person queries
- Admissions, courses, events, placements, campus, fees, student life, college history
- ANY question about a single specific person, role, or position

Key distinction: "who is the HOD of CSE" = NOT_SQL (single person). "list all HODs" = SQL (multiple people).
Key distinction: "former Principals" = SQL (from former_people table). "current Principal" = NOT_SQL (single person).
Key distinction: "students interested in chess" = SQL (bulk/filter set). "tell me about student X" = NOT_SQL.

Rules:
- ONLY generate SELECT statements. Never INSERT/UPDATE/DELETE.
- Return ONLY the raw SQL query or NOT_SQL. No explanation, no markdown, no backticks.

DATABASE SCHEMA:
{schema}

CONVERSATION HISTORY:
{chat_history}

USER QUESTION: {question}

Respond with ONLY the SQL query or NOT_SQL:""")

_sql_classify_chain = _SQL_CLASSIFY_PROMPT | llm | StrOutputParser()

# Max chars of chat history to send to the SQL classifier (~1500 chars ≈ 400 tokens)
_SQL_HISTORY_LIMIT = 1500


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


def classify_and_generate_sql(question: str, chat_history_text: str = "") -> str | None:
    """Ask the LLM if this question needs SQL. Returns SQL string or None."""
    normalized_question = normalize_user_query(question)
    mapped_question = map_query_to_preset(normalized_question)
    expanded_question = expand_creator_query(expand_query(mapped_question))

    # Fast path: explicit single-person student lookups should not depend on RAG.
    direct_student_sql = _student_single_lookup_sql(normalized_question)
    if direct_student_sql:
        return direct_student_sql

    # Hard gate: only attempt SQL for explicit bulk faculty/former/student asks.
    if not _is_bulk_entity_query(expanded_question):
        return None

    # Truncate history to avoid blowing the token limit — the classifier
    # only needs recent conversational context, not full SQL result tables.
    trimmed_history = chat_history_text[-_SQL_HISTORY_LIMIT:] if chat_history_text else ""
    result = _sql_classify_chain.invoke({
        "schema": _FACULTY_SCHEMA,
        "question": expanded_question,
        "chat_history": trimmed_history,
    }).strip()

    # Clean up: strip markdown fences if model adds them
    if result.startswith("```"):
        result = result.strip("`").strip()
        if result.lower().startswith("sql"):
            result = result[3:].strip()

    if result.upper() == "NOT_SQL" or not result.upper().startswith("SELECT"):
        return None
    return result


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
        conn = sqlite3.connect(FACULTY_DB)
        cur = conn.cursor()
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
        conn.close()
        return columns, rows
    except Exception as e:
        print(f"  [SQL Error] {e}")
        return None


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

    # Table output
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


# ============ END FACULTY SQL ============

# ============ RETRIEVAL FUNCTIONS ============
# BM25 handles keyword/exact matching natively, vector handles semantics.
# EnsembleRetriever combines both — no manual keyword maps needed.

def retrieve_context(inputs):
    """Hybrid BM25 + Vector retrieval with cross-encoder reranking."""
    question = normalize_user_query(inputs["question"])
    mapped_question = map_query_to_preset(question)
    expanded = expand_creator_query(expand_query(mapped_question))

    is_list = is_list_query(mapped_question)
    creator_intent = is_creator_query(mapped_question)
    active = retriever_large if (is_list or creator_intent) else retriever
    # Over-retrieve candidates for the reranker to score
    candidate_k = 80 if creator_intent else (60 if is_list else 25)
    final_k = 20 if creator_intent else (35 if is_list else 10)

    docs = active.invoke(expanded)
    candidates = docs[:candidate_k]
    reranked = rerank_docs_with_creator_boost(mapped_question, candidates, top_k=final_k, creator_intent=creator_intent)
    return format_docs(reranked)


def retrieve_with_metadata(question):
    """Retrieve docs with reranking and return (formatted_context, list_of_chunk_ids, num_docs)."""
    question = normalize_user_query(question)
    mapped_question = map_query_to_preset(question)
    expanded = expand_creator_query(expand_query(mapped_question))
    is_list = is_list_query(mapped_question)
    creator_intent = is_creator_query(mapped_question)
    active = retriever_large if (is_list or creator_intent) else retriever
    candidate_k = 80 if creator_intent else (60 if is_list else 25)
    final_k = 20 if creator_intent else (35 if is_list else 10)

    docs = active.invoke(expanded)
    candidates = docs[:candidate_k]
    reranked = rerank_docs_with_creator_boost(mapped_question, candidates, top_k=final_k, creator_intent=creator_intent)

    chunk_ids = []
    for doc in reranked:
        cid = doc.metadata.get("sub_chunk", doc.metadata.get("chunk_id", "?"))
        chunk_ids.append(cid)

    context_str = format_docs(reranked)
    return context_str, chunk_ids, len(reranked)


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


def retrieve_supporting_urls(question, limit=6):
    """Retrieve likely relevant docs for link-heavy queries and return direct URLs."""

    question = normalize_user_query(question)
    q = (question or "").lower()
    tokens = set(re.findall(r"[a-z0-9]{3,}", q))
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "what", "when", "where",
        "which", "show", "list", "give", "need", "want", "have", "has", "all", "about",
        "your", "their", "there", "into", "stats", "stat", "data", "details", "please",
    }
    query_terms = sorted(t for t in tokens if t not in stop)

    required = []
    if "placement" in q:
        required.extend(["placement", "placements"])
    if "stat" in q or "report" in q or "pdf" in q:
        required.extend(["statistics", "stats", "report", "pdf", "view statistics"])

    url_docs = []
    for d in docs:
        txt = d.page_content or ""
        has_url = bool(URL_PATTERN.search(txt))
        if not has_url:
            md = d.metadata or {}
            for _, value in md.items():
                if isinstance(value, str) and URL_PATTERN.search(value):
                    has_url = True
                    break
                if isinstance(value, list) and any(isinstance(v, str) and URL_PATTERN.search(v) for v in value):
                    has_url = True
                    break
        if has_url:
            url_docs.append(d)

    scored = []
    for d in url_docs:
        text = (d.page_content or "").lower()
        cats = str((d.metadata or {}).get("categories", "")).lower()
        has_placement_category = "placement" in cats

        if "placement" in q:
            placement_markers = [
                "placement statistics",
                "placement stats",
                "year-wise placement reports",
                "view statistics pdf",
                "academic year reports",
            ]
            if not any(marker in text for marker in placement_markers) and not has_placement_category:
                continue

        term_hits = sum(1 for t in query_terms if t in text)
        required_hits = sum(1 for t in required if t in text)
        score = term_hits + (required_hits * 3)
        if "placement" in q and has_placement_category:
            score += 4
        if score > 0:
            scored.append((score, d))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        top_docs = [d for _, d in scored[:50]]
        urls = _extract_urls_from_docs(top_docs, limit=limit)
        if urls:
            return urls

    # For placement/stat queries, avoid returning unrelated URLs from broad fallback.
    if "placement" in q:
        return []

    # Fallback to retrieval-based URL search if lexical scoring found nothing.
    expanded = expand_query(f"{question} pdf link url download")
    candidates = retriever_large.invoke(expanded)
    if not candidates:
        return []
    reranked = rerank_docs(f"{question} direct pdf link url", candidates[:80], top_k=35)
    return _extract_urls_from_docs(reranked, limit=limit)

# Chain
qa_chain = (
    {
        "context": RunnableLambda(retrieve_context),
        "question": itemgetter("question"),
        "chat_history": itemgetter("chat_history")
    } 
    | prompt 
    | llm 
    | StrOutputParser()
)

# Chain that accepts pre-built context (for when we retrieve separately)
qa_chain_with_context = (
    {
        "context": itemgetter("context"),
        "question": itemgetter("question"),
        "chat_history": itemgetter("chat_history")
    }
    | prompt
    | llm
    | StrOutputParser()
)

# Store for conversation history
chat_history = []