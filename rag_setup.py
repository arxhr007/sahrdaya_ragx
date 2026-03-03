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
import json
import os
import re
import hashlib
import pickle
import time
import sqlite3
from sentence_transformers import CrossEncoder


# ============ QUERY EXPANSION ============

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
}

def expand_query(question: str) -> str:
    """Expand abbreviated terms in the query for better embedding match."""
    expanded = question
    for pattern, replacement in _QUERY_EXPANSIONS.items():
        if re.search(pattern, question, re.IGNORECASE):
            expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
    return expanded


# ============ LOAD PRE-PROCESSED DATA ============
# Expects data_cleaned.jsonl produced by  python preprocess_data.py

CLEANED_FILE = "data_cleaned.jsonl"
RAW_FILE     = "data.txt"

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
    print("[*] Falling back to raw data.txt loading...")
    from langchain_community.document_loaders import DirectoryLoader, TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    loader = DirectoryLoader(".", glob="*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
    documents = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
    docs = text_splitter.split_documents(documents)
    print(f"[*] Created {len(docs)} chunks (unoptimized — run preprocess_data.py for better results)")

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
    """Hash of data_cleaned.jsonl to detect when data changes."""
    h = hashlib.md5()
    with open(CLEANED_FILE, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
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
# ------------------ GROQ API KEY (Replace with yours) ------------------
GROQ_API_KEY = ""

if not GROQ_API_KEY or GROQ_API_KEY.strip() == "":
    print("\n" + "="*70)
    print("ERROR: Groq API Key is missing!")
    print("="*70)
    print("\nPlease get your API key from:")
    print("🔗 https://console.groq.com/keys")
    print("\nThen add it to rag_setup.py (line ~210):")
    print('   GROQ_API_KEY = "gsk_..."')
    print("="*70 + "\n")
    exit(1)

llm = ChatGroq(
    groq_api_key=GROQ_API_KEY,
    model_name="openai/gpt-oss-120b"
)

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
                       "how many faculty", "how many professors", "tell me all"]
    return any(ind in q_lower for ind in list_indicators)

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
- Key leadership: Principal = Dr. Ramkumar S; Chairman = Mar Pauly Kannookadan; Executive Director = Fr. Dr. Anto Chungath.
- For LIST queries: show ALL matching items in a numbered list or table.
- Resolve pronouns using conversation history.
- If the answer is not in context, suggest contacting: admission@sahrdaya.ac.in / 0480 2759275.
- Redirect non-college queries to Sahrdaya topics.
- Be concise but complete.""")

# ============ FACULTY SQL DATABASE ============

FACULTY_DB = "faculty.db"

# Build DB on first import if it doesn't exist
if not os.path.exists(FACULTY_DB):
    print("[*] faculty.db not found — building from data.txt...")
    from faculty_db import build_db
    build_db(FACULTY_DB)
else:
    _conn = sqlite3.connect(FACULTY_DB)
    _cnt = _conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    _conn.close()
    print(f"[*] Faculty SQL database loaded ({_cnt} records)")

# ── Schema description for LLM ──────────────────────────────────────────────────

_FACULTY_SCHEMA = """
TABLE: faculty
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

DEPARTMENT ALIASES (map user terms to exact department values):
  cse, cs       -> 'Computer Science Engineering'
  ece           -> 'Electronics and Communication Engineering'
  eee           -> 'Electrical and Electronics Engineering'
  civil, ce     -> 'Civil Engineering'
  biotech, bt   -> 'Biotechnology Engineering'
  bme, biomed   -> 'Biomedical Engineering'
  ash           -> 'Applied Science and Humanities'
  mech, me      -> (no Mechanical dept in data currently)

IMPORTANT NOTES:
  - Use LIKE with %keyword% for department matching; do NOT use exact equality
  - For PhD queries: use has_phd = 1 for completed, phd_pursuing = 1 for pursuing
  - For HODs: WHERE designation LIKE '%Head%Department%'
  - Always ORDER BY name for consistent output
  - Use COUNT(*) for "how many" questions
  - Keep queries SELECT-only (read-only)
"""

# ── SQL classification + generation prompt ───────────────────────────────────────

_SQL_CLASSIFY_PROMPT = ChatPromptTemplate.from_template(
"""You are a query classifier for a college faculty database.

Given a user question, decide if it should be answered by querying the faculty SQL database.

The database contains ONLY faculty/staff information: names, departments, designations (roles),
PhD status, experience, publications, awards, patents, books, research areas, education, memberships.

IMPORTANT — Use SQL ONLY for BULK/LIST queries that need to retrieve or filter MULTIPLE faculty members.

Generate SQL for these types of queries:
- "list all CSE faculty" / "faculty of ECE" / "show all professors" → listing many faculty
- "CSE faculty with PhD" / "faculty with more than 5 publications" → filtered lists
- "how many faculty have PhD" / "count of ECE professors" → aggregate counts
- "faculty pursuing PhD in CSE" → filtered lists
- ANY query asking for a LIST, ALL, COUNT, or FILTERED SET of faculty

Respond NOT_SQL for these (let the RAG chatbot handle them naturally):
- "who is the HOD of CSE" / "who is the principal" → asking about ONE specific person
- "tell me about Dr. Raju G" / "who is minnuja" → individual person queries
- Admissions, courses, events, placements, campus, fees, student life, college history
- ANY question about a single specific person, role, or position

Key distinction: "who is the HOD of CSE" = NOT_SQL (single person). "list all HODs" = SQL (multiple people).

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


def classify_and_generate_sql(question: str, chat_history_text: str = "") -> str | None:
    """Ask the LLM if this question needs SQL. Returns SQL string or None."""
    # Truncate history to avoid blowing the token limit — the classifier
    # only needs recent conversational context, not full SQL result tables.
    trimmed_history = chat_history_text[-_SQL_HISTORY_LIMIT:] if chat_history_text else ""
    result = _sql_classify_chain.invoke({
        "schema": _FACULTY_SCHEMA,
        "question": question,
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


def execute_faculty_sql(sql: str) -> tuple[list[str], list[tuple]] | None:
    """Execute a read-only SQL query on faculty.db. Returns (column_names, rows) or None on error."""
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
        return "No matching faculty members found."

    # Single aggregate result (e.g. COUNT(*))
    if len(columns) == 1 and len(rows) == 1 and isinstance(rows[0][0], (int, float)):
        col = columns[0].replace("_", " ").title()
        return f"**{col}: {rows[0][0]}**"

    # Table output
    # Clean column names for display
    display_cols = []
    for c in columns:
        c = c.replace("_", " ").title()
        c = c.replace("Has Phd", "PhD").replace("Phd Pursuing", "PhD Pursuing")
        c = c.replace("Experience Years", "Experience (Yrs)")
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
    question = inputs["question"]
    expanded = expand_query(question)

    is_list = is_list_query(question)
    active = retriever_large if is_list else retriever
    # Over-retrieve candidates for the reranker to score
    candidate_k = 60 if is_list else 25
    final_k = 35 if is_list else 10

    docs = active.invoke(expanded)
    candidates = docs[:candidate_k]
    reranked = rerank_docs(question, candidates, top_k=final_k)
    return format_docs(reranked)


def retrieve_with_metadata(question):
    """Retrieve docs with reranking and return (formatted_context, list_of_chunk_ids, num_docs)."""
    expanded = expand_query(question)
    is_list = is_list_query(question)
    active = retriever_large if is_list else retriever
    candidate_k = 60 if is_list else 25
    final_k = 35 if is_list else 10

    docs = active.invoke(expanded)
    candidates = docs[:candidate_k]
    reranked = rerank_docs(question, candidates, top_k=final_k)

    chunk_ids = []
    for doc in reranked:
        cid = doc.metadata.get("sub_chunk", doc.metadata.get("chunk_id", "?"))
        chunk_ids.append(cid)

    context_str = format_docs(reranked)
    return context_str, chunk_ids, len(reranked)

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