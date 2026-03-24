# Frontend Integration Guide

This document explains how to connect a frontend chat UI to the Sahrdaya RAG FastAPI backend.

## Base URL

Use the production backend URL:

```text
https://ragx-backend.sahrdaya.ac.in
```

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



## JavaScript Example: Normal Chat

```javascript
const API_BASE = "https://ragx-backend.sahrdaya.ac.in";

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


```

## JavaScript Example: Streaming Chat

Because the stream endpoint uses `POST`, use `fetch()` with a readable stream instead of `EventSource`.

```javascript
const API_BASE = "https://ragx-backend.sahrdaya.ac.in";

export async function streamMessage({ sessionId, message, onEvent }) {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
    },
   Send each new user message with that `session_id`.
4. Render the returned `answer`.
5   }),
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



## Error Handling

Possible backend responses you should handle:

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
5. Use backend URL (`https://ragx-backend.sahrdaya.ac.in`) for all frontend requests.
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
