# Future Additions & Improvements

Things to fix, improve, and add to the RAG pipeline.

---

## Critical Fixes

- ✅ (DONE) **Persistent vector index** — FAISS index saved to `.index_cache/faiss/` with `vectorstore.save_local()`. Reloads in ~0.1s instead of rebuilding in ~50s. Cache invalidates automatically when `data/processed/data_cleaned.jsonl` changes (MD5 hash check)
- ✅ (DONE) **BM25 index persistence** — BM25 retrievers serialized to `.index_cache/bm25.pkl` with `pickle`. Loaded on subsequent runs, skipping full rebuild
- ✅ (DONE) **`.gitignore`** — Added with entries for `.index_cache/`, `__pycache__/`, `.env`, `venv/`, IDE folders, OS files, and debug scripts
- ✅ (DONE) **Shared SQL database build pipeline** — `sql_db_setup.py` now orchestrates `faculty_extractor.py`, `former_people_extractor.py`, and `student_db.py` into `data/sql/college.db`. Faculty parsing supports both legacy profile blocks and listing/card chunks
- ✅ (DONE) **SQL query routing** — LLM-based classifier in `rag_setup.py` routes bulk faculty queries to SQL and single-person/general queries to RAG. Includes schema-aware prompt, safety (SELECT-only), and automatic fallback to RAG on SQL failure
- ✅ (DONE) **Session analytics CLI** — `main.py` tracks per-query stats (response time, tokens, chunks) and provides `/graph` dashboard with ASCII bar charts, sparklines, chunk heatmaps, and context growth visualisation
- ✅ (DONE) **Index cache invalidation** — MD5 hash of `data/processed/data_cleaned.jsonl` stored alongside cached indexes. Automatically rebuilds when data changes

---

## Retrieval Improvements

- ✅ (DONE) **Cross-encoder reranker** — After hybrid retrieval, a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) reranks the top-K candidates. Over-retrieves 25-60 candidates then reranks to top 10-35. Runs locally (~22 MB model, ~50-100ms on CPU). Significantly improves precision by scoring full (query, document) pairs jointly
- 🔶 (Pending) **Metadata filtering** — Use category tags for pre-filtering (e.g., if the query mentions "admission", only search chunks tagged `admissions`). Currently categories are just text in the chunk — actual metadata filtering would be faster and more accurate
- 🔶 (Pending) **Better embedding model** — `all-MiniLM-L6-v2` is fast but small (384-dim). Upgrading to `bge-base-en-v1.5` or `e5-base-v2` (768-dim) would improve semantic accuracy at the cost of ~2x indexing time
- ✅ (DONE) **Query classification (SQL vs RAG)** — LLM-based classifier routes bulk faculty queries to SQL and everything else to RAG. Within the RAG path, list detection is still regex-based (`is_list_query`). A proper intent classifier (factual, list, comparison, opinion) would enable smarter routing within RAG itself
- 🔶 (Pending) **Hybrid weight tuning** — BM25:Vector ratio is hardcoded at 0.6:0.4. Should be tunable per query type, or learned from evaluation data

---

## Preprocessing Improvements

- 🔶 (Pending) **Hierarchical chunking** — Currently chunks are flat. Parent-child chunking (retrieve child, expand to parent for context) would give the LLM more surrounding context without bloating retrieval
- 🔶 (Pending) **Table-aware parsing** — Faculty tables and timetables should be parsed into structured formats, not treated as plain text. A dedicated table extractor would improve accuracy
- 🔶 (Pending) **Incremental preprocessing** — When a single page is re-scraped, currently the entire `data/processed/data_cleaned.jsonl` is regenerated. Should only reprocess changed chunks (use tracking hashes from scraper)
- 🔶 (Pending) **Deduplication** — Some pages have overlapping content. Add chunk-level deduplication (e.g., MinHash or exact hash) to remove near-duplicate chunks before indexing

---

## Evaluation & Testing

- 🔶 (Pending) **Ground truth Q&A dataset** — Create a set of 50-100 question-answer pairs with expected answers. Run automated evaluation to measure retrieval accuracy (recall@k) and answer quality
- 🔶 (Pending) **Retrieval metrics** — Track precision@k, recall@k, MRR (Mean Reciprocal Rank) for each query. Log which chunks were retrieved vs which were relevant
- 🔶 (Pending) **Regression testing** — Automated test suite that runs after every code change to catch regressions (like the Minnuja case). Current `_test_all.py` is a start but needs more coverage
- 🔶 (Pending) **A/B testing framework** — Compare different retrieval strategies (e.g., BM25 weight 0.5 vs 0.6) on the same query set and measure which performs better

---

## LLM & Generation

- ✅ (DONE) **Streaming responses** — API endpoint `/api/chat/stream` returns SSE events (`started`, `token`, `completed`) for incremental frontend rendering
- 🔶 (Pending) **Fallback model** — If Groq API is down or rate-limited, fall back to a local model (e.g., Ollama with Llama 3) or another API
- 🔶 (Pending) **Answer citation** — Include chunk IDs or source URLs in the response so users can verify where the answer came from. Chunk IDs are tracked internally (visible via `/chunks`) but not shown in the LLM's answer text
- 🔶 (Pending) **Guardrails** — Add input/output validation: block prompt injection attempts, filter inappropriate responses, enforce response length limits
- 🟡 (Partial) **Conversation memory limit** — *Partially done*: SQL classifier receives only the last ~1,500 chars of history, and SQL results >5 rows are stored as compact one-line summaries. However, the RAG prompt still receives full unbounded history. Need a rolling window or summarisation for the RAG path too

---

## Infrastructure & DevOps

- ✅ (DONE) **Docker container + compose deployment** — `Dockerfile`, `docker-compose.yml`, and `docker-compose.nginx.yml` support single-container or Nginx-balanced multi-replica deployment
- 🟡 (Partial) **Logging** — *Partially done*: `main.py` tracks per-query stats (response time, token estimates, chunk IDs, SQL queries) in `session_stats` and displays via `/stats` and `/graph`. Missing: persistent structured logging to file (JSON lines), query logs across sessions, error logging
- 🟡 (Partial) **Rate limiting** — Local RPM/TPM/RPD/TPD guardrails and load controls are implemented; global distributed rate limiting is still pending
- 🔶 (Pending) **Caching** — Cache responses for identical or near-identical queries. Many users ask the same questions ("who is the principal", "admission process")
- 🔶 (Pending) **Scheduled re-scraping** — Auto-scrape the website on a schedule (weekly/monthly) and reprocess data to keep the chatbot up to date
- ✅ (DONE) **API server** — FastAPI service is live with chat, streaming, sessions, health/readiness/load/limits endpoints

---

## Nice to Have

- 🔶 (Pending) **Multi-language support** — Handle Malayalam queries (many students might ask in their native language)
- 🟡 (Partial) **Document extraction strategy** — OCR is intentionally disabled; scraper now captures and labels PDF/DOC links for retrieval context. Full document-content parsing remains optional for future work
- 🔶 (Pending) **User feedback loop** — Let users rate answers as helpful/not helpful. Use this to identify weak spots in retrieval
- 🟡 (Partial) **Analytics dashboard** — *Partially done*: `/graph` command provides a per-session ASCII dashboard with response times, token usage, chunk heatmaps, and sparklines. Missing: persistent cross-session analytics, web-based dashboard, most-asked-questions tracking
- 🔶 (Pending) **Voice input** — Integrate speech-to-text for accessibility
- 🔶 (Pending) **Web frontend** — Build a chat UI (React/Next.js) that connects to the FastAPI backend
