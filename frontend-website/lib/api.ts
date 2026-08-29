// API configuration and utility functions for RAG backend

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "https://ragx-backend.sahrdaya.ac.in").replace(/\/$/, "")

export class ApiError extends Error {
  status: number
  retryAfterSeconds?: number

  constructor(message: string, status: number, retryAfterSeconds?: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
  }
}

function extractRetryAfterSeconds(text: string | null | undefined): number | undefined {
  if (!text) return undefined
  const m = text.match(/retry after\s*~?(\d+)\s*seconds/i)
  if (!m) return undefined
  const n = Number.parseInt(m[1], 10)
  return Number.isFinite(n) && n > 0 ? n : undefined
}

async function parseError(res: Response, fallbackMessage: string): Promise<ApiError> {
  const retryAfterHeader = res.headers.get("retry-after")
  const headerRetryAfter = retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) : undefined
  const contentType = (res.headers.get("content-type") || "").toLowerCase()

  let message: string | null = null

  if (contentType.includes("application/json")) {
    const errorBody = await res.json().catch(() => null)
    const detail = errorBody?.detail
    if (typeof detail === "string" && detail.trim()) {
      message = detail.trim()
    }
  } else {
    const bodyText = (await res.text().catch(() => "")).trim()
    // Avoid surfacing raw HTML error pages directly in chat UI.
    if (bodyText && !/^<!doctype html/i.test(bodyText) && !/^<html/i.test(bodyText)) {
      message = bodyText
    }
  }

  if (!message && res.status === 429) {
    const retryPart = headerRetryAfter && Number.isFinite(headerRetryAfter)
      ? ` Retry after ~${headerRetryAfter} seconds.`
      : ""
    message = `Rate limit reached.${retryPart}`
  }

  const retryAfterSeconds = headerRetryAfter || extractRetryAfterSeconds(message)

  return new ApiError(message || fallbackMessage, res.status, retryAfterSeconds)
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch {
    // One quick retry for transient browser/network hiccups.
    await new Promise((resolve) => setTimeout(resolve, 250))
    try {
      return await fetch(input, init)
    } catch (secondError) {
      const reason = secondError instanceof Error ? secondError.message : "network request failed"
      throw new ApiError(
        `Cannot reach backend at ${API_BASE_URL}. Reason: ${reason}. Start FastAPI/Nginx and check NEXT_PUBLIC_API_URL/CORS settings.`,
        0,
      )
    }
  }
}

// Types
export interface ChatRequest {
  message: string
  session_id: string
  include_metadata?: boolean
}

export interface ChatResponse {
  session_id: string
  answer: string
  metadata?: {
    mode?: "sql" | "rag" | "graph_rag"
    response_time?: number
    prompt_tokens?: number
    response_tokens?: number
    history_tokens?: number
    context_tokens?: number
    num_docs?: number
    chunk_ids?: string[]
    key_used_hint?: string
  }
}

export interface SessionResponse {
  session_id: string
}

export interface HealthResponse {
  status: string
  rag_loaded?: boolean
  timestamp: string
}

// API Functions
export async function healthCheck(): Promise<HealthResponse> {
  const res = await apiFetch(`${API_BASE_URL}/api/health`)
  if (!res.ok) throw await parseError(res, "Health check failed")
  return res.json()
}

export async function createSession(): Promise<SessionResponse> {
  const res = await apiFetch(`${API_BASE_URL}/api/sessions`, {
    method: "POST",
  })
  if (!res.ok) {
    throw await parseError(res, "Failed to create session")
  }
  return res.json()
}

export async function sendMessage(request: ChatRequest): Promise<ChatResponse> {
  const res = await apiFetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!res.ok) {
    throw await parseError(res, "Failed to send message")
  }
  return res.json()
}

export async function clearSession(sessionId: string): Promise<{ status: string; message: string }> {
  const res = await apiFetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
  })
  if (res.status === 204) {
    return { status: "ok", message: "Session deleted" }
  }
  if (!res.ok) {
    throw await parseError(res, "Failed to clear session")
  }
  return res.json()
}
