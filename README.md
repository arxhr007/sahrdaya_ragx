# Sahrdaya RAG X — College Chatbot Backend

A Retrieval-Augmented Generation (RAG) chatbot backend for **Sahrdaya College of Engineering & Technology (SCET)**, Kodakara, Thrissur, Kerala. It answers questions about faculty, departments, admissions, placements, clubs, infrastructure, and more — all grounded in data scraped from the college website.

> **📚 Want to learn how it works?** Check [WORKING.md](docs/WORKING.md) for a complete technical breakdown of the RAG pipeline: scraping, preprocessing, hybrid retrieval (BM25 + Vector), SQL routing, index caching, and answer generation.
>
> **🚀 What's planned next?** See [FUTURE_ADDITIONS.md](docs/FUTURE_ADDITIONS.md) for the roadmap: streaming responses, Docker, web frontend, and more.
>
> **🧩 Connecting a frontend?** See [FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) for API payloads, session flow, streaming examples, and frontend integration patterns.

## Quick Run

```bash
git clone <repo-url>
cd ragx-backend
pip install -r requirements.txt
```

Copy environment template and add your Groq key(s):

```bash
copy .env.example .env
```

Then start API server:

```bash
python api_main.py
```

Or start the load-balanced Docker stack:

```bash
docker compose -f docker-compose.nginx.yml up --build -d
```

## Full Architecture

### Combined RAG + API + Deployment Architecture

```mermaid
flowchart TD
    subgraph DEPLOY["🚢 Deployment Layer"]
        FE["🌐 Frontend Client"] --> LB["⚖️ Nginx LB :8080"]
        CFG["docker-compose.nginx.yml + .env"] --> API1["🐳 rag-api-1"]
        CFG --> API2["🐳 rag-api-2"]
        CFG --> API3["🐳 rag-api-3"]
        API1 --> LB
        API2 --> LB
        API3 --> LB
        HC["Healthchecks gate traffic"] --> LB
        LB --> CHAT["POST /api/chat\nPOST /api/chat/stream"]
        LB --> OPS["GET /api/health /ready /load /limits"]
        ERR["502/429/503"] --> RETRY["Client retry + backoff"]
    end

    subgraph API["🧩 FastAPI Runtime Layer"]
        CHAT --> SS["In-memory Session Store"]
        CHAT --> RL["Local Rate + Load Guardrails"]
        CHAT --> KP["Busy-key Failover Pool"]
        CHAT --> LOG["JSONL Chat Logger"]
        SS --> R
        RL --> R
        KP --> R
        LOG --> EV["logs/events.jsonl"]
        LOG --> IPL["logs/<client_ip>.jsonl"]
        LOG --> ROT["Rotating files\n5MB x 7 backups"]
        OPS --> OBS["Metrics + status endpoints"]
        OBS --> ERR
    end

    subgraph INGESTION["🕷️ Data Ingestion Layer"]
        A["🌐 sahrdaya.ac.in"] -->|"sitemap<br/>discovery"| B["🗺️ URL Queue"]
        B -->|"4 threads"| C["🎭 Playwright<br/>Renderer"]
        C -->|"open modals<br/>click Stats/Download"| C2["📎 Popup Link<br/>Capture"]
        C2 -->|"inject discovered<br/>PDF URLs"| D["🧹 BS4<br/>Cleaner"]
        C -->|"JS execution<br/>+ DOM parse"| D
        D --> E["📄 data/raw/sahrdaya_rag.txt<br/>~2K chunks"]
    end

    subgraph ETL["⚙️ ETL Pipeline"]
        E -->|"NLTK sentence<br/>tokenizer"| F["✂️ Sentence<br/>Splitter"]
        F -->|"regex +<br/>pattern match"| G["🏷️ Category<br/>Tagger"]
        G -->|"alias<br/>injection"| H["📦 data/processed/data_cleaned.jsonl<br/>18 categories"]
        E -->|"role-label<br/>parsing"| FP["👥 Former People<br/>Structurer"]
        FP -->|"10 role chunks<br/>+ summary"| H
        E -->|"legacy + listing<br/>parsing"| I["👤 Faculty<br/>Extractor"]
        E -->|"former people<br/>parsing"| IP["👥 Former People<br/>Parser"]
        STCSV["🧾 data/students.csv"] --> STING["👩‍🎓 student_db.py<br/>Normalizer + Loader"]
        I -->|"~109 profiles<br/>16 columns"| K["🗃️ SQLite DB<br/>data/sql/college.db"]
        IP -->|"52 records<br/>10 roles"| K
        STING -->|"students + interests + links<br/>incl. photo/projects"| K
    end

    subgraph INDEX["📊 Dual Index Layer"]
        H -->|"TF-IDF<br/>tokenization"| L["🔍 BM25<br/>Retriever"]
        H -->|"all-MiniLM-L6-v2<br/>384-dim"| M["🧠 FAISS<br/>Vector Store"]
        L -.->|"pickle"| N[("💾 .index_cache/")]
        M -.->|"save_local()"| N
        N -.->|"MD5<br/>validation"| O{{"♻️ Cache<br/>Hit?"}}
        O -->|"yes"| P["⚡ Fast Load<br/>~0.1s"]
        O -->|"no"| Q["🔨 Rebuild<br/>~50s"]
    end

    subgraph RUNTIME["❓ Query Runtime"]
        R(["🧑 User<br/>Query"]) --> S["📝 Chat<br/>History"]
        R --> SF["👤 Student Name<br/>Fast Path"]
        SF -->|"match"| V
        SF -->|"no match"| T
        S --> T{"🧠 LLM<br/>Classifier"}

        subgraph SQL["📊 SQL Path"]
            T -->|"bulk / aggregate /<br/>faculty + former + students"| U["🔧 Schema-Aware<br/>SQL Generator"]
            K --> U
            U -->|"SELECT only<br/>safety filter"| V["⚡ SQLite<br/>Executor"]
            V --> W["📋 Formatted SQL<br/>Result"]
            V -->|"error"| X["🔄 Fallback<br/>to RAG"]
        end

        subgraph RAG["📚 RAG Path"]
            T -->|"single-person /<br/>general"| Y["🔎 Query<br/>Expansion"]
            X --> Y
            Y --> Z["⚡ Ensemble<br/>Retriever"]
            L -->|"weight: 0.6"| Z
            M -->|"weight: 0.4"| Z
            Z -->|"RRF<br/>fusion"| AA["📊 Top-25<br/>Candidates"]
            AA --> AB["🤖 Cross-Encoder<br/>ms-marco-MiniLM"]
            AB -->|"rerank"| AC["🎯 Top-10<br/>Relevant"]
            AC --> AD["💬 Groq LLM<br/>Generation"]
        end
    end

    subgraph OUTPUT["✅ Output Layer"]
        W --> AE(["📨 Response"])
        AD --> AE
        AE --> AF["📊 Token<br/>Counter"]
        AE --> AG["🔢 Chunk<br/>Tracker"]
        AF & AG --> AH["📈 Session<br/>Analytics"]
        AH --> AI["📉 /graph<br/>Dashboard"]
        AH --> AJ["📋 /stats<br/>Summary"]
    end

    style INGESTION fill:none,stroke:#4a9eff,stroke-width:2px
    style ETL fill:none,stroke:#22c55e,stroke-width:2px
    style INDEX fill:none,stroke:#a855f7,stroke-width:2px
    style RUNTIME fill:none,stroke:#f97316,stroke-width:2px
    style API fill:none,stroke:#ff7f50,stroke-width:2px
    style DEPLOY fill:none,stroke:#7b61ff,stroke-width:2px
    style SQL fill:none,stroke:#ef4444,stroke-width:1px,stroke-dasharray: 5 5
    style RAG fill:none,stroke:#06b6d4,stroke-width:1px,stroke-dasharray: 5 5
    style OUTPUT fill:none,stroke:#eab308,stroke-width:2px
```

| File | Role |
|---|---|
| `scraper.py` | Multi-threaded web scraper (Playwright + Sitemap, popup-aware PDF link capture, thread-safe, 4 output formats) |
| `data/raw/sahrdaya_rag.txt` | Raw scraped chunks (TSV: `chunk_id\tcontent`) |
| `preprocess_data.py` | Cleans, categorises (18 categories), sentence-splits, injects search aliases, and structures former people data |
| `data/processed/data_cleaned.jsonl` | Optimised chunks ready for indexing |
| `sql_db_setup.py` | Orchestrates shared SQL DB build and runs faculty/former people/student loaders into one SQLite file |
| `faculty_extractor.py` | Parses faculty data from raw chunks (supports both legacy profile blocks and current listing-style chunks) |
| `former_people_extractor.py` | Parses and inserts former office-bearers into `former_people` |
| `student_db.py` | Loads `data/students.csv`, normalizes interests, and populates `students`, `interests`, `student_interests` |
| `data/students.csv` | Student source data (bio/biography, interests, photo URL, social links, projects links) |
| `data/sql/college.db` | Shared SQLite database for faculty, former people, students, and canonical interests |
| `sql_smoke_test.py` | Quick DB validation (schema + row sanity checks after ingestion/parser changes) |
| `rag_setup.py` | Builds FAISS + BM25 indexes, routes SQL vs RAG, includes single-student fast lookup, and formats SQL output |
| `main.py` | Interactive CLI chatbot with stats, ASCII dashboard, and session analytics |
| `api/` | FastAPI app split into `core`, `routes`, and `services` layers |
| `api/services/chat_logger.py` | JSON Lines chat logging (success + error), per-IP files, rotating handler with retention |
| `api_main.py` | API entrypoint (Uvicorn) |
| `.env` / `.env.example` | Runtime settings (keys, limits, CORS, concurrency) |
| `Dockerfile` | Container image for API service |
| `docker-compose.yml` | Single-container deployment |
| `docker-compose.nginx.yml` | 3 API containers + Nginx load balancing |
| `deploy/nginx-docker.conf` | Nginx upstream/load-balancer config for Docker |
| `logs/` | Runtime JSONL logs: `events.jsonl` (all events) + `<client_ip>.jsonl` (per-IP) |

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- A **Groq API key** set via `.env` (`GROQ_API_KEY` or `GROQ_API_KEYS`)
- ~500 MB disk space for embeddings model download on first run
- **Playwright** browsers (only needed for scraping): `playwright install`

## Setup

### 1. Clone the repo

```bash
git clone <repo-url>
cd ragx-backend
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Key packages: `langchain`, `langchain-community`, `langchain-classic`, `langchain-groq`, `langchain-huggingface`, `faiss-cpu`, `rank-bm25`, `nltk`, `groq`, `beautifulsoup4`, `playwright`.

### 4. Download NLTK data (auto-handled, but can be done manually)

```python
import nltk
nltk.download("punkt_tab")
```

## Usage

### Step 1 — Scrape (only if you need fresh data)

```bash
# Full site crawl
python scraper.py https://www.sahrdaya.ac.in/ -o sahrdaya --threads 8 --use-playwright

# Single page append (example: placement page with modal PDF links)
python scraper.py https://www.sahrdaya.ac.in/traning-and-placement -o sahrdaya --single --use-playwright
```

This produces `data/raw/sahrdaya_rag.txt`, which is the default input for preprocessing and DB setup.
For modal-driven pages (Stats -> Download/Open External), the scraper also captures discovered PDF URLs and stores them as Document Links in the raw output.

### Step 2 — Preprocess

```bash
python preprocess_data.py
```

Reads `data/raw/sahrdaya_rag.txt`, cleans text, detects categories, splits into sentence-aware chunks, injects search aliases, structures former people data into per-role chunks, and writes `data/processed/data_cleaned.jsonl`.

Sample output:
```
[1/4] Loaded 785 raw chunks from data/raw/sahrdaya_rag.txt
[2/4] Cleaned text — kept 784 chunks, skipped 1 near-empty
[3/4] Categorised & re-chunked — 2198 final chunks (466 large chunks were split)
[4/4] Wrote 2198 chunks to data/processed/data_cleaned.jsonl
```

### Step 3 — Run the chatbot

```bash
python main.py
```

On first run, FAISS and BM25 indexes are built from `data/processed/data_cleaned.jsonl` (~50s). Subsequent runs load from `.index_cache/` in ~0.1s. The cache auto-invalidates when the data file changes (MD5 hash check).

If `data/sql/college.db` doesn't exist, it's auto-built from `data/raw/sahrdaya_rag.txt` on startup.
Student data from `data/students.csv` is also loaded at startup into `students`, `interests`, and `student_interests` in the same DB.
For a quick integrity check after parser or ingestion updates, run `python sql_smoke_test.py`.

Student profile SQL output includes: name, graduation year, department, bio, photo URL, Instagram, GitHub, projects links, LinkedIn, and website.

### Step 4 — Run the FastAPI chatbot server

Copy environment template and add your keys:

```bash
copy .env.example .env
```

Then start API server:

```bash
python api_main.py
```

API endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/chat` | POST | Chat request-response API |
| `/api/chat/stream` | POST | SSE streaming events |
| `/api/sessions` | POST | Create new chat session |
| `/api/sessions/{session_id}/history` | GET | Get session chat history |
| `/api/sessions/{session_id}` | DELETE | Delete session |
| `/api/health` | GET | Liveness check |
| `/api/ready` | GET | Readiness check |
| `/api/load` | GET | Current in-flight load |
| `/api/limits` | GET | Local quota usage + key health |

The API includes:
- in-memory session isolation
- busy-key failover (switch to next key if one key is rate-limited)
- local quota guardrails for RPM/TPM/RPD/TPD
- full-answer policy (no API-side output truncation; continuation attempts if model stops due length)

### Step 5 — Run with Docker (recommended for different computers)

#### Option A: Single API container

```bash
docker compose up --build -d
```

Access API at:

```text
http://127.0.0.1:8000
```

Useful commands:

```bash
docker compose logs -f rag-api
docker compose down
```

#### Option B: Nginx + 3 API containers (local load balancing)

```bash
docker compose -f docker-compose.nginx.yml up --build -d
```

Access API through Nginx at:

```text
http://127.0.0.1:8080
```

Useful commands:

```bash
docker compose -f docker-compose.nginx.yml logs -f
docker compose -f docker-compose.nginx.yml down
```

Docker files added:
- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- `docker-compose.nginx.yml`
- `deploy/nginx-docker.conf`

## Deployment Pipeline

This repo already includes an end-to-end deployment path from source code to a health-gated, load-balanced runtime.

```mermaid
flowchart LR
     A[Code changes] --> B[Build API image from Dockerfile]
     B --> C[Start rag-api-1, rag-api-2, rag-api-3]
     C --> D[Health checks on /api/health]
     D --> E{All healthy?}
     E -->|Yes| F[Start Nginx reverse proxy]
     E -->|No| G[Keep service out of traffic]
     F --> H[Serve traffic on :8080]
```

### Pipeline Stages

1. Build
    - Command: `docker compose -f docker-compose.nginx.yml build`
    - Source: `Dockerfile`

2. Deploy
    - Command: `docker compose -f docker-compose.nginx.yml up -d`
    - Starts 3 API containers and 1 Nginx container

3. Health gate
    - Each API replica must pass `GET /api/health`
    - Nginx waits for healthy replicas via `depends_on: condition: service_healthy`

4. Traffic serving
    - Nginx listens on `:8080`
    - Requests are balanced across `rag-api-1`, `rag-api-2`, and `rag-api-3` using least-connections

5. Verify
    - `curl http://127.0.0.1:8080/api/health`
    - `docker compose -f docker-compose.nginx.yml ps`
    - `docker compose -f docker-compose.nginx.yml logs -f`

### Update Rollout (safe restart)

When you push code updates:

```bash
docker compose -f docker-compose.nginx.yml build
docker compose -f docker-compose.nginx.yml up -d
docker compose -f docker-compose.nginx.yml ps
```

This rebuilds images, recreates containers, and only sends traffic to healthy API instances.

### Optional CI/CD Trigger

If you connect this to GitHub Actions, use this same sequence as your deploy job on `main` branch pushes:

1. Checkout code
2. Build images (`docker compose ... build`)
3. Deploy (`docker compose ... up -d`)
4. Health probe (`/api/health`)
5. Fail job if health probe fails

## CLI Commands

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/graph` | Session dashboard with ASCII charts (response times, token usage, chunk heatmap) |
| `/chunks` | Show chunks used in last retrieval |
| `/history` | Show conversation history |
| `/stats` | Re-show last query stats box |
| `/clear` | Clear conversation history |
| `/reset` | Reset session stats and history |
| `exit` | Quit the program |

## Project Structure

```
ragx-backend/
├── scraper.py              # Web scraper (multi-threaded, Playwright, popup-aware PDF link capture)
├── data/
│   ├── raw/
│   │   ├── sahrdaya_rag.txt
│   │   ├── sahrdaya_raw.txt
│   │   ├── sahrdaya_structured.json
│   │   └── sahrdaya_tracking.json
│   ├── processed/
│   │   └── data_cleaned.jsonl
│   ├── sql/
│   │   └── college.db
│   └── students.csv        # Student profile source data
├── preprocess_data.py      # Data preprocessing pipeline
├── sql_db_setup.py         # Shared SQLite DB setup (faculty + former + students)
├── faculty_extractor.py    # Faculty parser/loader (legacy + listing formats)
├── former_people_extractor.py # Former people parser/loader
├── student_db.py           # Student CSV loader + interest normalization
├── sql_smoke_test.py       # SQL ingestion sanity test
├── rag_setup.py            # RAG engine (indexes, chains, SQL classifier)
├── main.py                 # CLI chatbot with session analytics
├── api/
│   ├── app.py              # FastAPI app bootstrap + middleware wiring
│   ├── core/
│   │   ├── models.py       # Pydantic request/response schemas
│   │   └── settings.py     # Environment-backed configuration
│   ├── routes/
│   │   └── chat.py         # API endpoints (/api/chat, sessions, health)
│   └── services/
│       ├── key_pool.py     # Busy-key failover state
│       ├── load_control.py # Concurrency and queue controls
│       ├── rate_limit_manager.py # Local RPM/TPM/RPD/TPD budget tracking
│       └── session_store.py # In-memory session memory with TTL
├── api_main.py             # Uvicorn run entrypoint
├── .env.example            # Environment template
├── Dockerfile              # Docker image build
├── docker-compose.yml      # Single API deployment
├── docker-compose.nginx.yml # Nginx + 3 API replicas
├── deploy/
│   ├── nginx.conf          # Bare-metal/local nginx config
│   └── nginx-docker.conf   # Docker nginx config
├── docs/
│   ├── CONTRIBUTING.md     # Contribution rules and legal notes
│   ├── FRONTEND_INTEGRATION.md # Frontend integration guide
│   ├── WORKING.md          # Technical documentation — how the RAG works
│   └── FUTURE_ADDITIONS.md # Roadmap and planned improvements
├── tests/
│   └── test_api.py         # API tests
├── requirements.txt        # Python dependencies
├── .index_cache/           # Cached FAISS + BM25 indexes (auto-generated)
│   ├── faiss/              # FAISS vector index
│   ├── bm25.pkl            # BM25 retriever (k=8)
│   ├── bm25_large.pkl      # BM25 retriever (k=50)
│   └── data_hash.txt       # MD5 hash for cache invalidation
└── README.md               # Setup and usage guide
```

## Configuration

| Setting | Default | File | Notes |
|---|---|---|---|
| LLM | Groq `openai/gpt-oss-120b` | `rag_setup.py` | Requires Groq API key |
| Embeddings | `all-MiniLM-L6-v2` (384-dim) | `rag_setup.py` | Runs locally, no API key |
| Chunk size | 700 chars target, 910 split threshold | `preprocess_data.py` | Sentence-aware splitting |
| Former people | 10 role-based chunks + 1 summary | `preprocess_data.py` | Structured per-role parsing for accurate retrieval |
| BM25:Vector weights | 0.6:0.4 | `rag_setup.py` | BM25 weighted higher for keyword queries |
| Max context | 22,000 chars (~6K tokens) | `rag_setup.py` | Truncates retrieved chunks to fit |
| SQL history limit | 1,500 chars | `rag_setup.py` | Caps history sent to SQL classifier |
| API host/port | `0.0.0.0:8000` | `.env` | Controlled by `API_HOST`, `API_PORT` |
| API concurrency | `4` | `.env` | `MAX_CONCURRENT_REQUESTS` for load control |
| Queue wait timeout | `20s` | `.env` | `QUEUE_WAIT_SECONDS` before busy response |
| Key failover | Enabled | `.env` + `api/services/key_pool.py` | Uses `GROQ_API_KEYS` pool with cooldown |
| Local quota guardrails | RPM/TPM/RPD/TPD | `.env` + `api/services/rate_limit_manager.py` | Protects service before upstream limits |

## License

This repository is **All Rights Reserved**.

- You may contribute to this repository through pull requests and approved collaboration workflows.
- You may not copy, reuse, redistribute, relicense, or sell this code outside this repository without prior written permission from the copyright holder.

See `LICENSE` for full legal terms.
