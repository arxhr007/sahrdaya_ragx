# Limiter Context (Temporary Public Testing Guardrail)

This document explains the temporary chat limiter added for public testing, and exactly which files were modified so another AI (or developer) can remove it cleanly later.

---

## ⚠️ TESTING NOTICE

**This rate limiter is TEMPORARY for public testing only and will be completely removed in production.**

### Limiter Behavior
- **Limit**: 5 chat requests per 5 minutes (per IP + per session)
- **What happens**: After 5 requests within a 5-minute sliding window, you'll be rate limited with a "Rate limited. Try again in Xs." message
- **Reset**: The limit resets automatically on a sliding 5-minute window. Once you've waited for the countdown timer to reach 0, you'll have new quota again
- **Result**: This ensures fair usage during public testing. It will not exist in production once testing is complete

### User Experience
When rate limited, you'll see:
1. A countdown banner showing "Rate limited. Try again in Xs." where X decreases every second
2. Chat input field and send button become disabled during the countdown
3. Once the timer reaches 0, you can send messages again immediately

---

## What Was Added

Two limiter layers currently exist:

1. App-level limiter (FastAPI, in-memory):
- Cap: 5 chat requests per 5 minutes
- Keys checked: `ip:<client_ip>` and `session:<session_id>`
- Applied on both endpoints:
  - `POST /api/chat`
  - `POST /api/chat/stream`
- Introspection endpoint added:
  - `GET /api/quota?session_id=<optional>` (non-consuming quota check)
- Returns `HTTP 429` with retry seconds
- Logs limiter rejections as JSONL `chat_error` with `error_type=client_window_limit`

2. Edge/global limiter (Nginx):
- Added because app-level memory is per replica and can be bypassed across 3 containers
- Enforced at Nginx before upstream routing
- Configured on:
  - `location = /api/chat`
  - `location = /api/chat/stream`
- Uses `limit_req_zone` + `limit_req` and returns `429`
- Handles CORS and OPTIONS preflight at edge to ensure browser can read limiter errors

## Why Both Layers Were Needed

- With 3 API replicas, app-only in-memory limiting behaves like per-container limits.
- Nginx limiter enforces one global limit per client IP across all replicas.

## Files Edited For Limiter

Core limiter code/config:
- `api/services/client_window_limiter.py` (new file)
- `api/routes/chat.py`
- `api/core/models.py`
- `api/core/settings.py`
- `deploy/nginx-docker.conf`
- `api/app.py` (CORS middleware removed; CORS now handled by Nginx)
- `.env`
- `.env.example`

Frontend integration (runtime + docs):
- `frontend-website/lib/api.ts`
- `frontend-website/components/chat-interface.tsx`
- `docs/FRONTEND_INTEGRATION.md`

Related documentation updates:
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

### Quota Status Endpoint
- `GET /api/quota?session_id=<optional>` in `api/routes/chat.py`
- Uses non-consuming limiter checks (`check_multi`) and returns:
  - `allowed`
  - `blocked_scope`
  - `retry_after_seconds`
  - `remaining_effective`
  - per-scope details (`used`, `remaining`, `reset_after_seconds`, etc.)
- Response models are in `api/core/models.py`:
  - `QuotaScopeStatus`
  - `QuotaStatusResponse`

### Nginx-level
- In `deploy/nginx-docker.conf`:
  - `limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=1r/m;`
  - `limit_req zone=chat_limit burst=4 nodelay;`
  - `limit_req_status 429;`
  - CORS headers added with `always` to include 429 responses
  - `OPTIONS` preflight short-circuited with `204`

### Frontend Runtime Handling
- `frontend-website/lib/api.ts`:
  - default base URL uses `http://localhost:8080`
  - robust 429 parsing (header + message)
  - `ApiError.retryAfterSeconds`
  - `getQuotaStatus(sessionId?)`
- `frontend-website/components/chat-interface.tsx`:
  - pre-send quota check
  - countdown lock state
  - disables input/send during lock window
  - shows visible retry countdown banner

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

4. Remove quota status endpoint:
- In `api/routes/chat.py`, remove `GET /api/quota`
- In `api/core/models.py`, remove:
  - `QuotaScopeStatus`
  - `QuotaStatusResponse`
- In `api/services/client_window_limiter.py`, remove:
  - `check`
  - `check_multi`

5. CORS ownership rollback decision:
- Current state: CORS is handled by Nginx, not FastAPI.
- If you remove Nginx CORS, you must re-enable FastAPI CORS middleware in `api/app.py`.

6. Optional frontend cleanup (if limiter UX should be removed):
- `frontend-website/lib/api.ts`: remove `retryAfterSeconds` logic + `getQuotaStatus`
- `frontend-website/components/chat-interface.tsx`: remove countdown lock UI and quota pre-check

7. Optional doc cleanup:
- Remove limiter mention from `README.md` if it was documented there.
- Remove limiter sections from `docs/FRONTEND_INTEGRATION.md`.

8. Redeploy after rollback:

```bash
docker compose -f docker-compose.nginx.yml down
docker compose -f docker-compose.nginx.yml up --build -d
```

## Notes / Caveats

- App-level limiter state is in-memory and resets on container restart.
- Nginx limiter is per source IP seen by Nginx.
- If both Nginx and FastAPI inject CORS headers, browser may reject with duplicate `Access-Control-Allow-Origin` values.
- If traffic comes through another proxy/CDN, ensure real client IP is preserved correctly.
