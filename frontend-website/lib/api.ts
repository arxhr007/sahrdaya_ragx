// API configuration and utility functions for RAG backend

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080").replace(/\/$/, "")

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
    mode?: string
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

export interface UpdateRequest {
  url: string
  output_prefix?: string
  use_playwright?: boolean
  max_pages?: number
}

export interface UpdateResponse {
  status: string
  message: string
  url: string
  pages_scraped?: number
  chunks_created?: number
}

export interface SiteInfo {
  url: string
  title: string
  description: string
  content_hash: string
  chunk_count: number
  word_count: number
  char_count: number
  scraped_at: string
}

export interface DashboardResponse {
  total_sites: number
  total_chunks: number
  sites: SiteInfo[]
  last_updated?: string
}

export interface HealthResponse {
  status: string
  rag_loaded?: boolean
  timestamp: string
}

export interface Session {
  session_id: string
  message_count: number
}

export interface SessionsResponse {
  sessions: Session[]
}

export interface QuotaScopeStatus {
  max_requests: number
  window_seconds: number
  used: number
  remaining: number
  retry_after_seconds: number
  reset_after_seconds: number
  blocked: boolean
}

export interface QuotaStatusResponse {
  allowed: boolean
  blocked_scope: string | null
  retry_after_seconds: number
  max_requests: number
  window_seconds: number
  remaining_effective: number
  scopes: Record<string, QuotaScopeStatus>
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

export async function updateData(request: UpdateRequest): Promise<UpdateResponse> {
  const res = await apiFetch(`${API_BASE_URL}/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(error.detail || "Failed to update data")
  }
  return res.json()
}

export async function getDashboard(): Promise<DashboardResponse> {
  const res = await apiFetch(`${API_BASE_URL}/dashboard`)
  if (!res.ok) throw new Error("Failed to fetch dashboard")
  return res.json()
}

export async function getSiteDetails(url: string): Promise<SiteInfo & { chunk_previews: Array<{ id: string; preview: string; char_count: number }> }> {
  const res = await apiFetch(`${API_BASE_URL}/dashboard/${encodeURIComponent(url)}`)
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(error.detail || "Failed to fetch site details")
  }
  return res.json()
}

export async function deleteSite(url: string): Promise<{ status: string; message: string }> {
  const res = await apiFetch(`${API_BASE_URL}/dashboard/${encodeURIComponent(url)}`, {
    method: "DELETE",
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(error.detail || "Failed to delete site")
  }
  return res.json()
}

export async function getSessions(): Promise<SessionsResponse> {
  const res = await apiFetch(`${API_BASE_URL}/sessions`)
  if (!res.ok) throw new Error("Failed to fetch sessions")
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

export async function reloadRag(): Promise<{ status: string; message: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reload`, {
    method: "POST",
  })
  if (!res.ok) throw new Error("Failed to reload RAG")
  return res.json()
}

export async function getQuotaStatus(sessionId?: string): Promise<QuotaStatusResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""
  const res = await apiFetch(`${API_BASE_URL}/api/quota${query}`)
  if (!res.ok) throw await parseError(res, "Failed to fetch quota status")
  return res.json()
}
