# Limiter Context (Temporary Public Testing Guardrail)

This document explains the temporary chat limiter added for public testing, and exactly which files were modified so another AI (or developer) can remove it cleanly later.

## What Was Added

Two limiter layers currently exist:

1. App-level limiter (FastAPI, in-memory):
- Cap: 5 chat requests per 5 minutes
- Keys checked: `ip:<client_ip>` and `session:<session_id>`
- Applied on both endpoints:
  - `POST /api/chat`
  - `POST /api/chat/stream`
- Returns `HTTP 429` with retry seconds
- Logs limiter rejections as JSONL `chat_error` with `error_type=client_window_limit`

2. Edge/global limiter (Nginx):
- Added because app-level memory is per replica and can be bypassed across 3 containers
- Enforced at Nginx before upstream routing
- Configured on:
  - `location = /api/chat`
  - `location = /api/chat/stream`
- Uses `limit_req_zone` + `limit_req` and returns `429`

## Why Both Layers Were Needed

- With 3 API replicas, app-only in-memory limiting behaves like per-container limits.
- Nginx limiter enforces one global limit per client IP across all replicas.

## Files Edited For Limiter

Core limiter code/config:
- `api/services/client_window_limiter.py` (new file)
- `api/routes/chat.py`
- `api/core/settings.py`
- `deploy/nginx-docker.conf`
- `.env`
- `.env.example`

Related documentation updates (not required for runtime):
- `README.md`

## Exact Environment Variables Introduced

Added to `.env` and `.env.example`:

- `CHAT_WINDOW_MAX_REQUESTS=5`
- `CHAT_WINDOW_SECONDS=300`

These map to settings in `api/core/settings.py`:
- `chat_window_max_requests`
- `chat_window_seconds`

## Runtime Behavior

### App-level (FastAPI)
- In `api/routes/chat.py`, before processing chat:
  - builds `limit_keys = [f"ip:{client_ip}", f"session:{session_id}"]`
  - calls `client_window_limiter.consume_multi(limit_keys)`
  - on reject: raises `HTTPException(429, detail=...)`

### Nginx-level
- In `deploy/nginx-docker.conf`:
  - `limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=1r/m;`
  - `limit_req zone=chat_limit burst=4 nodelay;`
  - `limit_req_status 429;`

## How To Remove Limiter Later (Rollback Plan)

1. Remove app-level limiter file and wiring:
- Delete file `api/services/client_window_limiter.py`
- In `api/routes/chat.py`:
  - remove import `ClientWindowLimiter`
  - remove `client_window_limiter = ClientWindowLimiter(...)`
  - remove `limit_keys` + `consume_multi(...)` blocks in both `/chat` and `/chat/stream`
  - remove `client_window_limit` error logging block (optional)

2. Remove settings/env vars:
- In `api/core/settings.py`, remove:
  - `chat_window_max_requests`
  - `chat_window_seconds`
- In `.env` and `.env.example`, remove:
  - `CHAT_WINDOW_MAX_REQUESTS`
  - `CHAT_WINDOW_SECONDS`

3. Remove Nginx limiter:
- In `deploy/nginx-docker.conf`, remove:
  - `limit_req_zone ...`
  - dedicated `location = /api/chat` limiter block
  - dedicated `location = /api/chat/stream` limiter block
- Keep standard proxy `location /` block as before

4. Optional doc cleanup:
- Remove limiter mention from `README.md` if it was documented there.

5. Redeploy after rollback:

```bash
docker compose -f docker-compose.nginx.yml down
docker compose -f docker-compose.nginx.yml up --build -d
```

## Notes / Caveats

- App-level limiter state is in-memory and resets on container restart.
- Nginx limiter is per source IP seen by Nginx.
- If traffic comes through another proxy/CDN, ensure real client IP is preserved correctly.
