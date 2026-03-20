# Frontend Integration Guide

This document explains how to connect a frontend chat UI to the Sahrdaya RAG FastAPI backend.

## Base URL

Use Nginx gateway only (recommended and required for limiter behavior parity):

```text
http://127.0.0.1:8080
```

## Temporary Public-Test Limiter

⚠️ **This limiter is TEMPORARY for public testing only and will be completely removed in production.**

For public testing, chat endpoints are temporarily capped to reduce abuse.

- Limit: **5 chat requests per 5 minutes**
- Applied on:
  - `POST /api/chat`
  - `POST /api/chat/stream`
- Scope:
  - per client IP (global at Nginx)
  - plus app-level IP/session checks
- **Reset Behavior**: The limit uses a sliding 5-minute window. Once the countdown timer reaches 0, you'll have fresh quota and can send messages again immediately.
- **Frontend UX**: When rate limited, you'll see a countdown banner ("Rate limited. Try again in Xs."), input disabled, and the countdown timer updates live every second.

When exceeded, backend returns `HTTP 429` with a message like:

```json
{
  "detail": "Temporary chat limit reached for this ip. Only 5 chats per 5 minutes are allowed. Retry after ~238 seconds."
}
```

Frontend should parse the retry seconds, show countdown, and disable the send action until retry is allowed.

## Limiter Source Code (Exact Snippets)

Use this section as a direct handoff for frontend AI tools so they can understand exactly how the limiter behaves.

### 1) App-level Sliding Window Limiter

```python
# api/services/client_window_limiter.py
import threading
import time
from collections import deque


class ClientWindowLimiter:
  def __init__(self, max_requests: int = 5, window_seconds: int = 300) -> None:
    self.max_requests = max(1, int(max_requests))
    self.window_seconds = max(1, int(window_seconds))
    self._lock = threading.Lock()
    self._hits: dict[str, deque[float]] = {}

  def _now(self) -> float:
    return time.time()

  def _prune(self, now: float, key: str) -> deque[float]:
    q = self._hits.get(key)
    if q is None:
      q = deque()
      self._hits[key] = q

    cutoff = now - self.window_seconds
    while q and q[0] <= cutoff:
      q.popleft()

    if not q:
      self._hits.pop(key, None)
      q = deque()
      self._hits[key] = q

    return q

  def consume(self, key: str) -> tuple[bool, int]:
    now = self._now()
    with self._lock:
      q = self._prune(now, key)
      if len(q) >= self.max_requests:
        retry_after = int(max(1, self.window_seconds - (now - q[0])))
        return False, retry_after
      q.append(now)
      return True, 0

  def consume_multi(self, keys: list[str]) -> tuple[bool, int, str | None]:
    now = self._now()
    unique_keys = [k for k in dict.fromkeys(keys) if k]
    if not unique_keys:
      unique_keys = ["ip:unknown"]

    with self._lock:
      for key in unique_keys:
        q = self._prune(now, key)
        if len(q) >= self.max_requests:
          retry_after = int(max(1, self.window_seconds - (now - q[0])))
          return False, retry_after, key

      for key in unique_keys:
        self._hits[key].append(now)

      return True, 0, None
```

### 2) Limiter Settings

```python
# api/core/settings.py (snippet)
chat_window_max_requests: int = Field(default=5, alias="CHAT_WINDOW_MAX_REQUESTS")
chat_window_seconds: int = Field(default=300, alias="CHAT_WINDOW_SECONDS")
```

```dotenv
# .env / .env.example
CHAT_WINDOW_MAX_REQUESTS=5
CHAT_WINDOW_SECONDS=300
```

### 3) Chat Endpoint Enforcement

```python
# api/routes/chat.py (snippet)
client_window_limiter = ClientWindowLimiter(
  max_requests=settings.chat_window_max_requests,
  window_seconds=settings.chat_window_seconds,
)

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
  session_store.cleanup()
  session_id = session_store.get_or_create(req.session_id)
  client_ip = _resolve_client_ip(request)

  limit_keys = [f"ip:{client_ip}", f"session:{session_id}"]
  allowed, retry_after, blocked_key = client_window_limiter.consume_multi(limit_keys)
  if not allowed:
    scope = "session" if (blocked_key or "").startswith("session:") else "ip"
    detail = (
      f"Temporary chat limit reached for this {scope}. "
      f"Only {settings.chat_window_max_requests} chats per {settings.chat_window_seconds // 60} minutes are allowed. "
      f"Retry after ~{retry_after} seconds."
    )
    try:
      chat_logger.log_error(
        client_ip=client_ip,
        session_id=session_id,
        question=req.message,
        status_code=429,
        error_type="client_window_limit",
        error_message=detail,
      )
    except Exception:
      pass
    raise HTTPException(status_code=429, detail=detail)
```

```python
# api/routes/chat.py (same logic for streaming)
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
  session_store.cleanup()
  session_id = session_store.get_or_create(req.session_id)
  client_ip = _resolve_client_ip(request)

  limit_keys = [f"ip:{client_ip}", f"session:{session_id}"]
  allowed, retry_after, blocked_key = client_window_limiter.consume_multi(limit_keys)
  if not allowed:
    scope = "session" if (blocked_key or "").startswith("session:") else "ip"
    detail = (
      f"Temporary chat limit reached for this {scope}. "
      f"Only {settings.chat_window_max_requests} chats per {settings.chat_window_seconds // 60} minutes are allowed. "
      f"Retry after ~{retry_after} seconds."
    )
    try:
      chat_logger.log_error(
        client_ip=client_ip,
        session_id=session_id,
        question=req.message,
        status_code=429,
        error_type="client_window_limit",
        error_message=detail,
      )
    except Exception:
      pass
    raise HTTPException(status_code=429, detail=detail)
```

### 4) Nginx Global Limiter (Across All Replicas)

```nginx
# deploy/nginx-docker.conf (snippet)
http {
  # Global per-IP temporary chat cap for public testing.
  # 1r/m with burst=4 allows up to 5 immediate chat requests,
  # then returns 429 until tokens refill.
  limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=1r/m;

  server {
    listen 8080;

    # Enforce chat cap on both normal and streaming chat routes.
    location = /api/chat {
      limit_req zone=chat_limit burst=4 nodelay;
      limit_req_status 429;
      proxy_pass http://rag_backend;
    }

    location = /api/chat/stream {
      limit_req zone=chat_limit burst=4 nodelay;
      limit_req_status 429;
      proxy_pass http://rag_backend;
    }
  }
}
```

### 5) Frontend Contract Summary

- On limit hit, client receives `429` with detail text containing `Retry after ~N seconds`.
- Frontend should parse `N` and lock send UI with countdown.
- Limiter applies to both normal and streaming chat endpoints.

## Recommended Frontend Flow

1. Create a session when the chat page loads.
2. Store the returned `session_id` in frontend state.
3. Optionally call `GET /api/quota?session_id=<id>` to show remaining quota and reset countdown before send.
4. Send each new user message with that `session_id`.
5. Render the returned `answer`.
6. Optionally read chat history when re-opening an existing session.

## Endpoints

### 1. Create session

**Request**

```http
POST /api/sessions
```

**Response**

```json
{
  "session_id": "7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a"
}
```

### 2. Send chat message

**Request**

```http
POST /api/chat
Content-Type: application/json
```

```json
{
  "message": "Who is the principal?",
  "session_id": "7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a",
  "include_metadata": true
}
```

**Response**

```json
{
  "session_id": "7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a",
  "answer": "Dr. Ramkumar S is the Principal of Sahrdaya College of Engineering & Technology.",
  "metadata": {
    "mode": "rag",
    "response_time": 1.42,
    "prompt_tokens": 6,
    "response_tokens": 18,
    "history_tokens": 0,
    "context_tokens": 420,
    "num_docs": 1,
    "chunk_ids": ["chunk_123"],
    "key_used_hint": "gsk_...abcd"
  }
}
```

### 3. Stream chat response

**Request**

```http
POST /api/chat/stream
Content-Type: application/json
```

```json
{
  "message": "Tell me about admissions",
  "session_id": "7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a"
}
```

This endpoint returns `text/event-stream` with these event types:

- `started`
- `token`
- `completed`

**Example stream events**

```text
event: started
data: {"session_id":"7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a"}

event: token
data: {"text":"Admissions at Sahrdaya..."}

event: token
data: {"text":"You can contact..."}

event: completed
data: {"session_id":"...","answer":"full answer text","metadata":{...}}
```

### 4. Get session history

**Request**

```http
GET /api/sessions/{session_id}/history
```

**Response**

```json
{
  "session_id": "7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a",
  "turns": [
    {
      "role": "user",
      "content": "Who is the principal?",
      "timestamp": 1742123456.12
    },
    {
      "role": "assistant",
      "content": "Dr. Ramkumar S is the Principal...",
      "timestamp": 1742123457.01
    }
  ]
}
```

### 5. Delete session

**Request**

```http
DELETE /api/sessions/{session_id}
```

**Response**

```http
204 No Content
```

### 6. Health and load endpoints

Useful for ops dashboards or frontend status banners:

- `GET /api/health`
- `GET /api/ready`
- `GET /api/load`
- `GET /api/limits`

### 7. Quota status endpoint

Use this endpoint to show users how much quota is left and how long until reset.

**Request**

```http
GET /api/quota?session_id={session_id}
```

`session_id` is optional but recommended.

**Response**

```json
{
  "allowed": true,
  "blocked_scope": null,
  "retry_after_seconds": 0,
  "max_requests": 5,
  "window_seconds": 300,
  "remaining_effective": 2,
  "scopes": {
    "ip:172.18.0.1": {
      "max_requests": 5,
      "window_seconds": 300,
      "used": 3,
      "remaining": 2,
      "retry_after_seconds": 0,
      "reset_after_seconds": 121,
      "blocked": false
    },
    "session:7a6617b2-7fd0-4a68-8bb4-d4e6af2c5c8a": {
      "max_requests": 5,
      "window_seconds": 300,
      "used": 1,
      "remaining": 4,
      "retry_after_seconds": 0,
      "reset_after_seconds": 254,
      "blocked": false
    }
  }
}
```

## JavaScript Example: Normal Chat

```javascript
const API_BASE = "http://127.0.0.1:8080";

export async function createSession() {
  const response = await fetch(`${API_BASE}/api/sessions`, {
    method: "POST",
  });

  if (!response.ok) {
    throw new Error("Failed to create session");
  }

  return response.json();
}

export async function sendMessage({ sessionId, message }) {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      include_metadata: true,
    }),
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    const detail = errorBody?.detail || "Chat request failed";
    const retryMatch = String(detail).match(/Retry after ~?(\d+) seconds/i);
    const retryAfterSeconds = retryMatch ? Number(retryMatch[1]) : 0;

    const err = new Error(detail);
    err.status = response.status;
    err.retryAfterSeconds = retryAfterSeconds;
    throw err;
  }

  return response.json();
}

export async function getQuotaStatus(sessionId) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(`${API_BASE}/api/quota${query}`);

  if (!response.ok) {
    throw new Error("Failed to fetch quota status");
  }

  return response.json();
}
```

## JavaScript Example: Streaming Chat

Because the stream endpoint uses `POST`, use `fetch()` with a readable stream instead of `EventSource`.

```javascript
const API_BASE = "http://127.0.0.1:8080";

export async function streamMessage({ sessionId, message, onEvent }) {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
    },
    body: JSON.stringify({
      session_id: sessionId,
      message,
    }),
  });

  if (!response.ok || !response.body) {
    const errorBody = await response.json().catch(() => null);
    const detail = errorBody?.detail || "Streaming request failed";
    const retryMatch = String(detail).match(/Retry after ~?(\d+) seconds/i);
    const retryAfterSeconds = retryMatch ? Number(retryMatch[1]) : 0;

    const err = new Error(detail);
    err.status = response.status;
    err.retryAfterSeconds = retryAfterSeconds;
    throw err;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
      let eventName = "message";
      let data = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          eventName = line.slice(7);
        }
        if (line.startsWith("data: ")) {
          data += line.slice(6);
        }
      }

      if (!data) continue;
      onEvent({ event: eventName, data: JSON.parse(data) });
    }
  }
}
```

## Example React Usage

```javascript
import { useEffect, useState } from "react";
import { createSession, sendMessage } from "./api";

export function ChatPage() {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    createSession().then((data) => setSessionId(data.session_id));
  }, []);

  async function handleSend(text) {
    if (!sessionId || !text.trim()) return;

    setMessages((current) => [...current, { role: "user", content: text }]);
    setLoading(true);

    try {
      const result = await sendMessage({ sessionId, message: text });
      setMessages((current) => [
        ...current,
        { role: "assistant", content: result.answer },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `Error: ${error.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return null;
}
```

## Tiny React Hook: 429 Countdown Lock

Use this hook to disable sending while waiting for limiter retry.

```javascript
import { useEffect, useMemo, useState } from "react";

export function useRetryLock() {
  const [retryUntil, setRetryUntil] = useState(0);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!retryUntil) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [retryUntil]);

  const retryAfterSeconds = useMemo(() => {
    if (!retryUntil) return 0;
    return Math.max(0, Math.ceil((retryUntil - now) / 1000));
  }, [retryUntil, now]);

  const isLocked = retryAfterSeconds > 0;

  function lockForSeconds(seconds) {
    const s = Number(seconds || 0);
    if (s <= 0) return;
    setRetryUntil(Date.now() + s * 1000);
  }

  function clearLock() {
    setRetryUntil(0);
  }

  return { isLocked, retryAfterSeconds, lockForSeconds, clearLock };
}
```

### Hook Usage with sendMessage

```javascript
import { useEffect, useState } from "react";
import { createSession, sendMessage } from "./api";
import { useRetryLock } from "./useRetryLock";

export function ChatPage() {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const { isLocked, retryAfterSeconds, lockForSeconds } = useRetryLock();

  useEffect(() => {
    createSession().then((data) => setSessionId(data.session_id));
  }, []);

  async function handleSend(text) {
    if (!sessionId || !text.trim() || loading || isLocked) return;

    setMessages((current) => [...current, { role: "user", content: text }]);
    setLoading(true);

    try {
      const result = await sendMessage({ sessionId, message: text });
      setMessages((current) => [
        ...current,
        { role: "assistant", content: result.answer },
      ]);
    } catch (error) {
      if (error?.status === 429 && error?.retryAfterSeconds) {
        lockForSeconds(error.retryAfterSeconds);
      }
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `Error: ${error.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {isLocked ? <p>Rate limited. Try again in {retryAfterSeconds}s.</p> : null}
      <button disabled={loading || isLocked}>Send</button>
    </>
  );
}
```

## Error Handling

Possible backend responses you should handle:

### 429 Too Many Requests

This means temporary chat limiter or quota guardrails were hit.

Example:

```json
{
  "detail": "Temporary chat limit reached for this ip. Only 5 chats per 5 minutes are allowed. Retry after ~238 seconds."
}
```

Frontend handling:

1. Show a retry message.
2. Disable send button until retry delay ends.
3. Show a countdown timer (recommended).
4. Re-enable input automatically when timer reaches zero.
5. Do not auto-resend by default; let user confirm resend.

### 503 Service Unavailable

This means all keys are busy or the server queue timed out.

Example:

```json
{
  "detail": "Server busy, queue timeout reached"
}
```

Frontend handling:

1. Show a temporary busy message.
2. Keep user message in input or retry queue.
3. Retry with backoff.

### 404 Not Found

This usually means session ID is invalid or expired.

Frontend handling:

1. Create a new session.
2. Ask user to resend the message.

## CORS

The backend reads allowed frontend origins from `.env`:

```text
API_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

If your frontend runs on another port or domain, add it there.

## Production Notes

1. Store `session_id` in memory or browser storage depending on UX.
2. Do not expose Groq keys in frontend code.
3. Use `/api/chat` for simpler integration first.
4. Move to `/api/chat/stream` when you want token-by-token UI updates.
5. Use Nginx base URL (`http://127.0.0.1:8080`) for all frontend requests.
6. For public demos, expose friendly limiter UI text ("Try again in 2m 10s") instead of raw backend detail.

## Quick Frontend Checklist

1. Create session on load.
2. Save `session_id`.
3. Send `message` plus `session_id` on each turn.
4. Render `answer`.
5. Handle `429`, `503`, and `404` cleanly.
6. Add origin to `API_CORS_ORIGINS` if frontend is blocked by CORS.
7. On `429`, read retry seconds and lock send button with countdown.
8. Poll `GET /api/quota` to show remaining chats and reset countdown in UI.
