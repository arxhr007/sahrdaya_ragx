# Annotations are not evaluated at def time, so signatures may reference names
# (Document, CrossEncoder, ...) that only exist after the lazy _initialize().
from __future__ import annotations

from operator import itemgetter
from functools import lru_cache
from collections import defaultdict
import json
import os
import re
from dotenv import load_dotenv
load_dotenv()
import hashlib
import pickle
import time
import sqlite3
import threading


from sql_extractors.student_db import ensure_student_data

# Query routing, SQL building and formatting live in rag_query.py: they depend only
# on the standard library, so they stay importable (and testable) without loading a
# single model. Re-exported here so existing callers keep importing from rag_setup.
from rag_query import (
    BM25_CACHE,
    BM25_LARGE_CACHE,
    CACHE_DIR,
    CLEANED_FILE,
    CREATOR_CANONICAL_LINE,
    CREATOR_QUERY_PATTERN,
    DB_HASH_FILE,
    FACULTY_DB,
    FAISS_DIR,
    GRAPH_HASH_FILE,
    GRAPH_ROUTE_PRINT,
    GRAPH_SCHEMA_V1,
    GRAPH_STOPWORDS,
    HASH_FILE,
    RAW_FILE,
    TRACKING_FILE,
    URL_PATTERN,
    _DEPARTMENT_CANONICAL_MAP,
    _FORMER_ROLE_CANONICAL_MAP,
    _PEOPLE_SUMMARY_COLUMNS,
    _QUERY_EXPANSIONS,
    _ROLE_CANONICAL_MAP,
    _ROLE_LOOKUP_COLUMNS,
    _bm25_preprocess,
    _cache_is_valid,
    _data_hash,
    _db_hash_is_current,
    _db_source_hash,
    _deterministic_query_map,
    _drop_empty_columns,
    _extract_interest_from_query,
    _extract_person_name_candidate,
    _extract_urls_from_docs,
    _extract_urls_from_text,
    _file_hash,
    _get_faculty_columns,
    _get_former_columns,
    _get_interests_columns,
    _get_student_interests_columns,
    _get_students_columns,
    _graph_data_hash,
    _graph_link_intent,
    _is_bulk_entity_query,
    _is_safe_mapped_query,
    _leadership_terms,
    _limit_people_columns,
    _load_tracking_pages,
    _looks_single_person_query,
    _normalize_name_for_match,
    _print_graphrag_used,
    _role_lookup_sql,
    _save_graph_hash,
    _save_hash,
    _student_single_lookup_sql,
    _tokenize_graph_query,
    _write_db_hash,
    execute_faculty_sql,
    expand_creator_query,
    expand_query,
    format_sql_results,
    is_creator_query,
    is_list_query,
    validate_faculty_sql,
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

GRAPH_ROUTE_PATTERN = re.compile(
    r"\b(graph|relationship|related|connect(?:ed|ion)?|link(?:ed|s)?|path|structure|"
    r"site\s*map|sitemap|navigation|which\s+page|where\s+can\s+i\s+find|"
    r"under\s+which|category|section|source\s+page|document\s+link)\b",
    re.IGNORECASE,
)





# ============ QUERY EXPANSION ============

_query_correct_chain = None
_query_map_chain = None
QUERY_PIPELINE_DEBUG = os.environ.get("QUERY_PIPELINE_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def normalize_user_query(question: str) -> str:
    """Apply LLM-based query correction before routing/retrieval."""
    ensure_engine()
    normalized = (question or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _llm_correct_query(normalized)


@lru_cache(maxsize=512)
def _llm_correct_query(query: str) -> str:
    """Use the configured LLM to correct spelling/typos while preserving intent."""
    ensure_engine()
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


















def map_query_to_preset(question: str) -> str:
    """Canonicalize natural-language query shape for reliable SQL/RAG routing."""
    ensure_engine()
    baseline = _deterministic_query_map(question)
    if not baseline:
        return ""
    if _query_map_chain is None:
        return baseline

    try:
        mapped = _query_map_chain.invoke({"question": baseline}).strip()
        mapped = mapped.strip("`\"'")
        mapped = re.sub(r"\s+", " ", mapped).strip()
        if not mapped:
            return baseline
        return mapped if _is_safe_mapped_query(baseline, mapped) else baseline
    except Exception:
        return baseline


def canonicalize_query_pipeline(question: str) -> tuple[str, str, str]:
    """Two-stage canonicalization contract: normalize -> map -> expand (+creator terms).

    The normalize and map stages are each an LLM round-trip, together adding ~15s to
    every query — enough to make an otherwise 2s SQL answer take 19s.

    The deterministic map recognises shorthand and role/department shapes by keyword, so
    it tolerates typos on its own ("who is teh hod of ece" still resolves). When it fires
    we already hold an exact canonical query and both LLM calls are pure overhead, so
    skip them. Queries it does not recognise still get the full treatment.
    """
    ensure_engine()
    raw = re.sub(r"\s+", " ", (question or "").strip())
    direct = _deterministic_query_map(raw)
    if direct and direct != raw:
        normalized, mapped = raw, direct
    else:
        normalized = normalize_user_query(question)
        mapped = map_query_to_preset(normalized)
    expanded = expand_creator_query(expand_query(mapped))
    if QUERY_PIPELINE_DEBUG:
        print("[query-pipeline]", {
            "original": (question or "").strip(),
            "normalized": normalized,
            "mapped": mapped,
            "expanded": expanded,
        })
    return normalized, mapped, expanded


# ============ LOAD PRE-PROCESSED DATA ============
# Expects data/processed/data_cleaned.jsonl produced by python preprocess_data.py


# Keep raw text for direct faculty extraction


# Add a canonical attribution chunk so creator/credits questions always have a stable source.

DOC_BY_SUBCHUNK: "dict[str, object]" = {}
DOCS_BY_PARENT: "dict[str, list]" = defaultdict(list)


def _build_doc_lookups() -> None:
    ensure_engine()
    DOC_BY_SUBCHUNK.clear()
    DOCS_BY_PARENT.clear()
    for doc in docs:
        md = doc.metadata or {}
        sub = str(md.get("sub_chunk", md.get("chunk_id", ""))).strip()
        parent = str(md.get("chunk_id", sub)).strip()
        if not sub:
            continue
        DOC_BY_SUBCHUNK[sub] = doc
        DOCS_BY_PARENT[parent].append(doc)



# ============ RETRIEVERS ============

# Embeddings (local, no API key needed)

GRAPH_CACHE      = os.path.join(CACHE_DIR, "content_graph.json")










def _graph_cache_is_valid() -> bool:
    ensure_engine()
    if nx is None or json_graph is None:
        return False
    if not all(os.path.exists(p) for p in [GRAPH_CACHE, GRAPH_HASH_FILE]):
        return False
    with open(GRAPH_HASH_FILE, "r", encoding="utf-8") as f:
        return f.read().strip() == _graph_data_hash()








def _build_content_graph():
    ensure_engine()
    if nx is None:
        return None

    graph = nx.MultiDiGraph()

    for doc in docs:
        md = doc.metadata or {}
        sub = str(md.get("sub_chunk", md.get("chunk_id", ""))).strip()
        if not sub:
            continue

        parent = str(md.get("chunk_id", sub)).strip()
        categories = str(md.get("categories", "general")).split(",")
        categories = [c.strip() for c in categories if c.strip()]
        chunk_node = f"chunk:{sub}"

        graph.add_node(
            chunk_node,
            node_type="chunk",
            chunk_id=sub,
            parent_chunk=parent,
            categories=",".join(categories),
            text=doc.page_content,
        )

        for category in categories:
            cat_node = f"category:{category}"
            graph.add_node(cat_node, node_type="category", name=category)
            graph.add_edge(chunk_node, cat_node, rel="tagged_as")
            graph.add_edge(cat_node, chunk_node, rel="has_chunk")

        for url in _extract_urls_from_text(doc.page_content):
            url_node = f"url:{url}"
            graph.add_node(url_node, node_type="url", url=url)
            graph.add_edge(chunk_node, url_node, rel="mentions_url")

    tracking_pages = _load_tracking_pages()
    for page_url, payload in tracking_pages.items():
        page_node = f"page:{page_url}"
        title = str(payload.get("title", ""))
        description = str(payload.get("description", ""))
        graph.add_node(
            page_node,
            node_type="page",
            url=page_url,
            title=title,
            description=description,
        )

        for parent_id in payload.get("chunk_ids", []):
            for doc in DOCS_BY_PARENT.get(str(parent_id), []):
                sub = str((doc.metadata or {}).get("sub_chunk", "")).strip()
                if not sub:
                    continue
                chunk_node = f"chunk:{sub}"
                if graph.has_node(chunk_node):
                    graph.add_edge(page_node, chunk_node, rel="contains_chunk")
                    graph.add_edge(chunk_node, page_node, rel="in_page")

        for document_link in payload.get("document_links", []):
            if isinstance(document_link, dict):
                url = str(document_link.get("url", "")).strip()
                label = str(document_link.get("purpose", document_link.get("label", ""))).strip()
            else:
                url = str(document_link).strip()
                label = ""

            if not url:
                continue

            url_node = f"url:{url}"
            graph.add_node(url_node, node_type="url", url=url, label=label)
            graph.add_edge(page_node, url_node, rel="has_document_link")

    return graph


def _load_or_build_content_graph():
    ensure_engine()
    if nx is None or json_graph is None:
        print("[*] GraphRAG disabled (networkx not available)")
        return None

    if _graph_cache_is_valid():
        try:
            print("[*] Loading cached content graph...")
            with open(GRAPH_CACHE, "r", encoding="utf-8") as f:
                graph_data = json.load(f)
            graph = json_graph.node_link_graph(graph_data)
            print(f"[*] Content graph loaded ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")
            return graph
        except Exception as exc:
            print(f"[!] Failed to load graph cache, rebuilding: {exc}")

    print("[*] Building content graph for GraphRAG...")
    graph = _build_content_graph()
    try:
        with open(GRAPH_CACHE, "w", encoding="utf-8") as f:
            json.dump(json_graph.node_link_data(graph), f, ensure_ascii=False)
        _save_graph_hash()
    except Exception as exc:
        print(f"[!] Failed to persist graph cache: {exc}")

    print(f"[*] Content graph ready ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")
    return graph




# Vector retrievers with MMR for diversity

# ============ CROSS-ENCODER RERANKER ============
# After hybrid retrieval returns candidates, the cross-encoder scores each
# (query, document) pair jointly — much more accurate than bi-encoder similarity.
# Model: ms-marco-MiniLM-L-6-v2 (~22 MB, runs locally, no API key needed)



def rerank_docs(query: str, docs: list, top_k: int) -> list:
    """Rerank retrieved documents using the cross-encoder.
    
    Scores each (query, doc) pair jointly and returns the top_k documents
    sorted by cross-encoder score (highest first).
    """
    ensure_engine()
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


# Max characters for context (to stay under token limit ~6000 tokens = ~24000 chars)
MAX_CONTEXT_CHARS = 22000

# Helper function to format documents with size limit
def format_docs(docs, max_chars=MAX_CONTEXT_CHARS):
    ensure_engine()
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







def rerank_docs_with_creator_boost(query: str, docs: list, top_k: int, creator_intent: bool) -> list:
    """Rerank with cross-encoder and apply a light lexical boost for creator intents."""
    ensure_engine()
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

# ============ FACULTY SQL DATABASE ============









# Build DB on first import if it doesn't exist

# ── Schema description for LLM ──────────────────────────────────────────────────

_FACULTY_SCHEMA = """
TABLE 1: faculty  (current faculty & staff)
COLUMNS:
  id               INTEGER PRIMARY KEY
  name             TEXT        -- faculty member's full name
  designation      TEXT        -- EXACT values in the data: 'Assistant Professor', 'Associate Professor',
                               -- 'Professor', 'Head of Department', 'Assistant HOD', 'Principal', 'Dean'
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
  - For HODs: WHERE designation LIKE '%Head of Department%'
  - ROLE QUESTIONS NAME ONE PERSON. 'who is the HOD of CSE' must filter on BOTH
    designation AND department and return that single person. Never answer a role
    question by listing the whole department.
      Correct: SELECT name, designation, department, email FROM faculty
               WHERE designation LIKE '%Head of Department%'
                 AND department LIKE '%Computer Science Engineering%'
      Wrong:   SELECT * FROM faculty WHERE department LIKE '%Computer Science Engineering%'
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


# Max chars of chat history to send to the SQL classifier (~1500 chars ≈ 400 tokens)
_SQL_HISTORY_LIMIT = 1500














def classify_and_generate_sql(question: str, chat_history_text: str = "") -> str | None:
    """Ask the LLM if this question needs SQL. Returns SQL string or None."""
    ensure_engine()
    normalized_question, mapped_question, expanded_question = canonicalize_query_pipeline(question)

    # Fast path: explicit single-person student lookups should not depend on RAG.
    direct_student_sql = _student_single_lookup_sql(normalized_question)
    if direct_student_sql:
        return direct_student_sql

    # Fast path: current leadership ("who is the HOD of CSE"). Deterministic so the
    # classifier cannot turn a one-person question into a department roster.
    role_sql = _role_lookup_sql(mapped_question)
    if role_sql:
        return role_sql

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
























# ============ END FACULTY SQL ============

# ============ RETRIEVAL FUNCTIONS ============
# BM25 handles keyword/exact matching natively, vector handles semantics.
# EnsembleRetriever combines both — no manual keyword maps needed.






def _is_graph_query(question: str) -> bool:
    ensure_engine()
    q = normalize_user_query(question)
    if not q:
        return False
    if is_creator_query(q):
        return False
    return bool(GRAPH_ROUTE_PATTERN.search(q))


def _seed_graph_chunks_from_query(tokens: list[str]) -> set[str]:
    ensure_engine()
    if not CONTENT_GRAPH or not tokens:
        return set()

    seeds: set[str] = set()
    for node, data in CONTENT_GRAPH.nodes(data=True):
        node_type = data.get("node_type")
        if node_type == "category":
            cat = str(data.get("name", "")).lower()
            if any(tok in cat for tok in tokens):
                for _, dst, edge_data in CONTENT_GRAPH.out_edges(node, data=True):
                    if edge_data.get("rel") != "has_chunk":
                        continue
                    dst_data = CONTENT_GRAPH.nodes.get(dst, {})
                    if dst_data.get("node_type") == "chunk":
                        cid = str(dst_data.get("chunk_id", "")).strip()
                        if cid:
                            seeds.add(cid)

        if node_type == "page":
            haystack = f"{data.get('title', '')} {data.get('description', '')} {data.get('url', '')}".lower()
            if any(tok in haystack for tok in tokens):
                for _, dst, edge_data in CONTENT_GRAPH.out_edges(node, data=True):
                    if edge_data.get("rel") != "contains_chunk":
                        continue
                    dst_data = CONTENT_GRAPH.nodes.get(dst, {})
                    if dst_data.get("node_type") == "chunk":
                        cid = str(dst_data.get("chunk_id", "")).strip()
                        if cid:
                            seeds.add(cid)

    return seeds


def _score_graph_chunk(question_tokens: list[str], doc: Document, seed_chunks: set[str]) -> int:
    ensure_engine()
    md = doc.metadata or {}
    sub = str(md.get("sub_chunk", md.get("chunk_id", ""))).strip()
    text = (doc.page_content or "").lower()
    cats = str(md.get("categories", "")).lower()
    score = 0

    for tok in question_tokens:
        if tok in text:
            score += 2
        if tok in cats:
            score += 2

    if sub and sub in seed_chunks:
        score += 6

    # Link-intent prompts benefit from chunks that explicitly contain URLs.
    if _graph_link_intent(" ".join(question_tokens)) and URL_PATTERN.search(doc.page_content or ""):
        score += 3

    return score


def retrieve_with_metadata_graph(question: str) -> tuple[str, list[str], int]:
    """Return graph-informed context for relationship/structure style questions."""
    ensure_engine()
    if not CONTENT_GRAPH:
        return "", [], 0

    normalized = normalize_user_query(question)
    tokens = _tokenize_graph_query(normalized)
    if not tokens:
        return "", [], 0

    seed_chunks = _seed_graph_chunks_from_query(tokens)
    scored_docs: list[tuple[int, Document]] = []
    for doc in docs:
        md = doc.metadata or {}
        sub = str(md.get("sub_chunk", md.get("chunk_id", ""))).strip()
        if sub == "canonical_creators":
            continue
        score = _score_graph_chunk(tokens, doc, seed_chunks)
        if score > 0:
            scored_docs.append((score, doc))

    if not scored_docs:
        return "", [], 0

    scored_docs.sort(key=lambda item: item[0], reverse=True)
    top_docs = [d for _, d in scored_docs[:16]]

    chunk_ids = [
        str((d.metadata or {}).get("sub_chunk", (d.metadata or {}).get("chunk_id", "?")))
        for d in top_docs
    ]
    context_str = format_docs(top_docs)
    return context_str, chunk_ids, len(top_docs)

def retrieve_context(inputs):
    """Route retrieval (GraphRAG or hybrid) and return context string."""
    ensure_engine()
    context, _, _, _ = retrieve_with_metadata(inputs["question"], return_mode=True)
    return context


def retrieve_with_metadata(question: str, return_mode: bool = False):
    """Route retrieval and return context/chunks/docs with optional route mode."""
    ensure_engine()
    if _is_graph_query(question):
        graph_context, graph_chunks, graph_count = retrieve_with_metadata_graph(question)
        if graph_count > 0:
            _print_graphrag_used(question, graph_count)
            if return_mode:
                return graph_context, graph_chunks, graph_count, "graph_rag"
            return graph_context, graph_chunks, graph_count

    """Fallback path: hybrid BM25 + vector + reranker."""
    _, mapped_question, expanded = canonicalize_query_pipeline(question)
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
    if return_mode:
        return context_str, chunk_ids, len(reranked), "rag"
    return context_str, chunk_ids, len(reranked)




def retrieve_supporting_urls(question, limit=6):
    """Retrieve likely relevant docs for link-heavy queries and return direct URLs."""
    ensure_engine()

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

# Store for conversation history.
# Owned by the CLI (main.py) only -- it is a plain unlocked module global shared by
# every importer, so it has no per-user scoping. API code must NOT touch this; the
# FastAPI layer keeps per-session history in api/services/session_store.SessionStore.

# ============ LAZY ENGINE ============
# Building the indexes, downloading the two sentence-transformer models and opening the
# Groq client used to happen at import time. That cost ~20s warm / ~136s cold on every
# import, wrote to college.db as a side effect, and called exit(1) when no API key was
# set — which killed unrelated entrypoints that merely imported this module.
#
# All of it now runs on first use. Everything the rest of the codebase reads
# (prompt, llm, retriever, CONTENT_GRAPH, qa_chain, ...) is resolved through the
# module-level __getattr__ below, so callers are unchanged.

_ENGINE_LOCK = threading.Lock()
_ENGINE_READY = False
_INIT_THREAD = None

# Names produced by _initialize(); requesting any of them triggers the build.
_LAZY_NAMES = frozenset({
    "raw_docs_text", "docs", "embeddings", "vectorstore", "vectorstore_large",
    "bm25_retriever", "bm25_retriever_large", "vector_retriever",
    "vector_retriever_large", "retriever", "retriever_large", "_reranker",
    "CONTENT_GRAPH", "GROQ_API_KEY", "llm", "prompt", "qa_chain",
    "qa_chain_with_context", "_QUERY_CORRECT_PROMPT", "_QUERY_MAP_PROMPT",
    "_SQL_CLASSIFY_PROMPT", "_sql_classify_chain", "_query_correct_chain",
    "_query_map_chain", "HuggingFaceEmbeddings", "FAISS", "BM25Retriever",
    "EnsembleRetriever", "ChatGroq", "RunnableLambda", "StrOutputParser",
    "ChatPromptTemplate", "Document", "CrossEncoder", "nx", "json_graph",
})


def engine_ready() -> bool:
    """True once the models, indexes and LLM client are loaded."""
    return _ENGINE_READY


def ensure_engine() -> None:
    """Build the retrieval engine if it has not been built yet. Idempotent.

    Functions in this module reference the lazy globals by plain name, and module
    __getattr__ only covers attribute access from outside, so each heavy entry point
    calls this first. _initialize() itself calls some of those functions, hence the
    re-entrancy guard: the building thread passes straight through, everyone else
    waits on the lock.
    """
    global _ENGINE_READY, _INIT_THREAD
    if _ENGINE_READY:
        return
    if _INIT_THREAD == threading.get_ident():
        return
    with _ENGINE_LOCK:
        if _ENGINE_READY:
            return
        _INIT_THREAD = threading.get_ident()
        try:
            _initialize()
            _ENGINE_READY = True
        finally:
            _INIT_THREAD = None


def __getattr__(name):
    # PEP 562: only called for names not already in module globals, so this costs
    # nothing once _initialize() has assigned them.
    if name in _LAZY_NAMES:
        ensure_engine()
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(
                "%r was not produced by rag_setup._initialize()" % name
            ) from None
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _initialize() -> None:
    """Load models, build/restore indexes, open the LLM client and the DB."""
    global BM25Retriever, CONTENT_GRAPH, ChatGroq, ChatPromptTemplate, CrossEncoder, Document
    global EnsembleRetriever, FAISS, GROQ_API_KEY, HuggingFaceEmbeddings, RunnableLambda, StrOutputParser
    global _QUERY_CORRECT_PROMPT, _QUERY_MAP_PROMPT, _SQL_CLASSIFY_PROMPT, _cnt, _conn, _fcnt
    global _has_former, _icnt, _pool_raw, _query_correct_chain, _query_map_chain, _reranker
    global _scnt, _sicnt, _sql_classify_chain, _student_stats, _t0, _t1
    global bm25_retriever, bm25_retriever_large, docs, documents, embeddings, json_graph
    global llm, loader, nx, prompt, qa_chain, qa_chain_with_context
    global raw_docs_text, retriever, retriever_large, text_splitter, vector_retriever, vector_retriever_large
    global vectorstore

    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_community.retrievers import BM25Retriever
    from langchain_classic.retrievers.ensemble import EnsembleRetriever
    from langchain_groq import ChatGroq
    from langchain_core.runnables import RunnableLambda
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.documents import Document
    from sentence_transformers import CrossEncoder

    try:
        import networkx as nx
        from networkx.readwrite import json_graph
    except Exception:
        nx = None
        json_graph = None

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

    _build_doc_lookups()

    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    os.makedirs(CACHE_DIR, exist_ok=True)

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

    CONTENT_GRAPH = _load_or_build_content_graph()

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

    print("[*] Loading cross-encoder reranker...")
    _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    print("[*] Cross-encoder reranker ready")

    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
    if not GROQ_API_KEY:
        _pool_raw = os.environ.get("GROQ_API_KEYS", "").strip()
        if _pool_raw:
            _pool = [k.strip() for k in _pool_raw.split(",") if k.strip()]
            if _pool:
                GROQ_API_KEY = _pool[0]

    if not GROQ_API_KEY:
        # Raise rather than exit(1): this runs on first use, and a library that kills
        # the interpreter takes down every caller that merely imported the module.
        raise RuntimeError(
            "Groq API key is missing.\n"
            "  Get one at https://console.groq.com/keys, then set it:\n"
            '    PowerShell : $env:GROQ_API_KEY = "gsk_..."\n'
            '    bash       : export GROQ_API_KEY="gsk_..."\n'
            '    or put GROQ_API_KEY=gsk_... (or GROQ_API_KEYS=gsk_1,gsk_2) in .env'
        )

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
    - Never convert a single-person question (e.g., "who is X") into a list/count query.
    - Keep entity scope unchanged (faculty/student/former-people must stay the same).
    - Return only one rewritten query line.

    User query: {question}
    """)

    _query_map_chain = _QUERY_MAP_PROMPT | llm | StrOutputParser()

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

    LINKS — follow this order exactly:
    - If asked for a document, PDF, regulation, form, handbook, placements report, syllabus, or
      statistics file: search the CONTEXT for URLs and print every relevant one.
    - Print raw URLs in plain text (starting with http:// or https://). Never hide a link behind
      a markdown label, and never invent a URL.
    - Do NOT say "no direct link was found" while any URL for the requested document appears in
      the context. Only say that when the context genuinely contains none.
    - The bare homepage https://sahrdaya.ac.in/ is a last resort for when the context has no
      specific link at all. It is not an answer to a request for a particular document, so never
      offer it in place of a link that exists in the context.

    SCOPE:
    - Answer only questions about Sahrdaya College. For anything else, say it is outside what you
      cover and point to https://sahrdaya.ac.in/.
    - This applies to requests to PERFORM A TASK as much as to questions of fact. Do not write
      poems, stories, essays, songs, jokes, code, or translations, and do not do general homework,
      even if asked to make them about the college. Decline briefly and redirect to college topics.
    - Never pad an answer with unrelated facts from the context to satisfy such a request.

    - If the answer is not in context, say so and suggest visiting: https://sahrdaya.ac.in/.
    - Be concise but complete.""")

    if not os.path.exists(FACULTY_DB):
        print("[*] data/sql/college.db not found — building from data/raw/sahrdaya_rag.txt...")
        from sql_db_setup import build_db
        build_db(FACULTY_DB)
        _write_db_hash()
    elif not _db_hash_is_current():
        print("[*] Source data changed since data/sql/college.db was built — rebuilding...")
        from sql_db_setup import build_db
        build_db(FACULTY_DB)
        _write_db_hash()
        _conn = sqlite3.connect(FACULTY_DB)
        _cnt = _conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
        _fcnt = _conn.execute("SELECT COUNT(*) FROM former_people").fetchone()[0]
        _conn.close()
        print(f"[*] Faculty SQL database rebuilt ({_cnt} faculty + {_fcnt} former people)")
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
            _write_db_hash()
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


chat_history = []