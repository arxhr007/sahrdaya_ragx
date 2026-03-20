import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_groq import ChatGroq

import rag_setup
from api.core.models import (
    ChatRequest,
    ChatResponse,
    LimitsResponse,
    LoadResponse,
    QuotaStatusResponse,
    SessionCreateResponse,
)
from api.core.settings import get_settings
from api.services.key_pool import KeyPool
from api.services.chat_logger import ChatLogger
from api.services.client_window_limiter import ClientWindowLimiter
from api.services.load_control import LoadController
from api.services.rate_limit_manager import RateLimitManager
from api.services.session_store import SessionStore


settings = get_settings()
router = APIRouter(prefix="/api", tags=["chat"])

session_store = SessionStore(ttl_seconds=settings.session_ttl_seconds)
rate_manager = RateLimitManager(
    rpm=settings.groq_rpm_limit,
    tpm=settings.groq_tpm_limit,
    rpd=settings.groq_rpd_limit,
    tpd=settings.groq_tpd_limit,
)
key_pool = KeyPool(
    keys=settings.parsed_keys(),
    failure_threshold=settings.key_failure_threshold,
    default_cooldown_seconds=settings.key_default_cooldown_seconds,
)
load_control = LoadController(
    max_concurrent=settings.max_concurrent_requests,
    queue_wait_seconds=settings.queue_wait_seconds,
)
client_window_limiter = ClientWindowLimiter(
    max_requests=settings.chat_window_max_requests,
    window_seconds=settings.chat_window_seconds,
)
chat_logger = ChatLogger()

URL_PATTERN = re.compile(r"https?://[^\s)\]\}>\"']+")


def _resolve_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    out.append(str(text))
        return "".join(out)
    return str(content)


def _build_history_text(turns: list[dict]) -> str:
    rows = []
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "assistant":
            rows.append(f"Assistant: {content}")
        else:
            rows.append(f"User: {content}")
    return "\n".join(rows)


def _parse_retry_after_seconds(error_text: str, default_value: int) -> int:
    m = re.search(r"retry-after[^0-9]*(\d+)", error_text, re.IGNORECASE)
    if m:
        return max(1, int(m.group(1)))
    return default_value


def _clean_sql_result(result: str) -> str | None:
    text = result.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("sql"):
            text = text[3:].strip()
    if text.upper() == "NOT_SQL" or not text.upper().startswith("SELECT"):
        return None
    return text


def _extract_urls(text: str, limit: int = 6) -> list[str]:
    found = URL_PATTERN.findall(text or "")
    out: list[str] = []
    seen: set[str] = set()

    def _is_static_asset(u: str) -> bool:
        low = u.lower().split("?")[0]
        return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".css", ".js"))

    def _priority(u: str) -> int:
        low = u.lower()
        if ".pdf" in low or "alt=media" in low:
            return 0
        if any(ext in low for ext in [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"]):
            return 1
        return 2

    candidates: list[str] = []
    for url in found:
        u = url.rstrip(".,;:)")
        if not u or u in seen or _is_static_asset(u):
            continue
        seen.add(u)
        candidates.append(u)

    for u in sorted(candidates, key=_priority):
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _query_likely_needs_links(query: str) -> bool:
    q = (query or "").lower()
    keywords = [
        "link", "links", "url", "download", "pdf", "document", "docs",
        "placement", "placements", "stats", "statistics", "report", "handbook",
        "regulation", "syllabus", "approval", "audit",
    ]
    return any(k in q for k in keywords)


def _format_fallback_links(query: str, urls: list[str]) -> str:
    q = (query or "").lower()
    unique: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    if "placement" not in q:
        return "Direct links from context:\n" + "\n".join(f"- {u}" for u in unique)

    year_links: list[tuple[str, str]] = []
    extra_links: list[str] = []
    for u in unique:
        low = u.lower()
        m = re.search(r"tpo%2fplacement%2fsah%2f(\d{4}-\d{2})", low)
        if not m:
            m = re.search(r"/tpo/placement/sah/(\d{4}-\d{2})", low)
        if m:
            year_links.append((m.group(1), u))
        else:
            extra_links.append(u)

    def _year_start(label: str) -> int:
        try:
            return int(label.split("-")[0])
        except Exception:
            return 9999

    year_links.sort(key=lambda x: _year_start(x[0]))

    lines = ["Verified placement report links (year-wise):"]
    for y, u in year_links:
        lines.append(f"- {y}: {u}")
    for u in extra_links:
        lines.append(f"- {u}")
    return "\n".join(lines)


def _harmonize_response_with_links(response: str, links_appended: bool) -> str:
    """Remove contradictory 'no direct URL' claims when links are present below."""
    if not links_appended:
        return response
    text = response or ""
    replacement_map = [
        (r"\*?No\s+direct\s+(?:URL|URLs|link|links?)\s+(?:was|were|is|are)\s+(?:present|provided)\s+in\s+the\s+context\.?\*?", "-"),
        (r"\*No URL provided in the context\*", "-"),
        (r"\*no direct urls? (?:are|were) (?:present|provided) in the context\*", "-"),
        (r"No direct URL \(if any\)\s*[:\-]?\s*No[^\n|.]*", "-"),
        (r"-\s*\*\*Download links\*\*[^\n]*", ""),
        (r"\*\*Download links\*\*[^\n]*", ""),
        (r"the context mentions[^\n]*actual download links?[^\n]*\.", ""),
    ]
    for pat, repl in replacement_map:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _invoke_with_key_failover(coro_factory):
    last_error = None
    attempts = max(1, len(key_pool.snapshot()))

    for _ in range(attempts):
        key = key_pool.acquire()
        if not key:
            break
        try:
            result = await coro_factory(key)
            key_pool.mark_success(key)
            return result, key
        except Exception as exc:
            error_text = str(exc)
            last_error = exc
            if "429" in error_text or "rate limit" in error_text.lower():
                retry_after = _parse_retry_after_seconds(error_text, settings.key_default_cooldown_seconds)
                key_pool.mark_busy(key, cooldown_seconds=retry_after, reason="rate_limited")
            else:
                key_pool.mark_failure(key, reason=error_text[:120])

    raise HTTPException(status_code=503, detail=f"No healthy API key available: {last_error}")


async def _classify_sql(question: str, history_text: str) -> tuple[str | None, str]:
    # Keep API behavior aligned with CLI:
    # 1) direct student single-name lookup SQL, 2) hard gate for bulk SQL intents.
    direct_student_sql = rag_setup._student_single_lookup_sql(question)
    if direct_student_sql:
        return direct_student_sql, ""

    if not rag_setup._is_bulk_entity_query(question):
        return None, ""

    trimmed_history = history_text[-rag_setup._SQL_HISTORY_LIMIT:] if history_text else ""

    async def _runner(key: str):
        llm = ChatGroq(groq_api_key=key, model_name=settings.groq_model_id)
        pv = rag_setup._SQL_CLASSIFY_PROMPT.invoke(
            {
                "schema": rag_setup._FACULTY_SCHEMA,
                "question": question,
                "chat_history": trimmed_history,
            }
        )
        return await asyncio.to_thread(llm.invoke, pv.to_messages())

    msg, key = await _invoke_with_key_failover(_runner)
    raw = _extract_text(msg.content).strip()
    return _clean_sql_result(raw), key


async def _answer_rag(question: str, history_text: str, context_str: str) -> tuple[str, str]:
    async def _runner(key: str):
        llm = ChatGroq(groq_api_key=key, model_name=settings.groq_model_id)
        base_prompt = rag_setup.prompt.invoke(
            {"question": question, "chat_history": history_text, "context": context_str}
        )
        messages = list(base_prompt.to_messages())
        parts: list[str] = []

        for _ in range(settings.max_continuations + 1):
            ai_msg = await asyncio.to_thread(llm.invoke, messages)
            text = _extract_text(ai_msg.content)
            if text:
                parts.append(text)
            finish_reason = (ai_msg.response_metadata or {}).get("finish_reason")
            if finish_reason not in {"length", "max_tokens"}:
                break
            messages.extend(
                [
                    AIMessage(content=text),
                    HumanMessage(content="Continue exactly from where you stopped. Do not repeat."),
                ]
            )

        return "".join(parts)

    answer, key = await _invoke_with_key_failover(_runner)
    return answer, key


async def _process_chat(req: ChatRequest, session_id: str, client_ip: str = "unknown") -> ChatResponse:
    acquired = await load_control.acquire()
    if not acquired:
        raise HTTPException(status_code=503, detail="Server busy, queue timeout reached")

    t0 = time.time()
    try:
        session_store.cleanup()
        turns = session_store.get_turns(session_id)
        history_text = _build_history_text(turns)

        est_tokens = rate_manager.estimate_tokens(req.message + history_text)
        allowed, retry_after = rate_manager.can_consume(est_tokens)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited locally. Retry after ~{int(retry_after)} seconds.",
            )

        sql_query, key_for_sql = await _classify_sql(req.message, history_text)
        mode = "rag"
        answer = ""
        chunk_ids: list[str] = []
        num_docs = 0
        context_tokens = 0
        key_hint = KeyPool.key_hint(key_for_sql)

        if sql_query and rag_setup.validate_faculty_sql(sql_query):
            sql_result = rag_setup.execute_faculty_sql(sql_query)
            if sql_result:
                cols, rows = sql_result
                if rows:
                    answer = rag_setup.format_sql_results(cols, rows, req.message)
                    mode = "sql"

        if not answer:
            context_str, chunk_ids, num_docs = rag_setup.retrieve_with_metadata(req.message)
            context_tokens = rate_manager.estimate_tokens(context_str)
            answer, key_used = await _answer_rag(req.message, history_text, context_str)
            if _query_likely_needs_links(req.message) and not URL_PATTERN.search(answer or ""):
                fallback_urls = _extract_urls(context_str, limit=6)
                if not fallback_urls:
                    fallback_urls = rag_setup.retrieve_supporting_urls(req.message, limit=6)
                if fallback_urls:
                    answer = answer.rstrip() + "\n\n" + _format_fallback_links(req.message, fallback_urls)
                    answer = _harmonize_response_with_links(answer, links_appended=True)
            key_hint = KeyPool.key_hint(key_used)

        session_store.append_turn(session_id, "user", req.message)
        session_store.append_turn(session_id, "assistant", answer)

        prompt_tokens = rate_manager.estimate_tokens(req.message)
        response_tokens = rate_manager.estimate_tokens(answer)
        history_tokens = rate_manager.estimate_tokens(history_text)
        rate_manager.consume(prompt_tokens + response_tokens + history_tokens + context_tokens)

        elapsed = time.time() - t0
        metadata = {
            "mode": mode,
            "response_time": elapsed,
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "history_tokens": history_tokens,
            "context_tokens": context_tokens,
            "num_docs": num_docs,
            "chunk_ids": chunk_ids,
            "key_used_hint": key_hint,
        }

        try:
            chat_logger.log_success(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                answer=answer,
                mode=mode,
                metadata=metadata,
            )
        except Exception:
            # Logging should never affect API response behavior.
            pass

        return ChatResponse(
            session_id=session_id,
            answer=answer,
            metadata=metadata if req.include_metadata else None,
        )
    finally:
        await load_control.release()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    keys_available = any(not k["busy"] for k in key_pool.snapshot())
    return {"ready": keys_available}


@router.get("/load", response_model=LoadResponse)
async def load() -> LoadResponse:
    snap = await load_control.snapshot()
    return LoadResponse(**snap)


@router.get("/limits", response_model=LimitsResponse)
async def limits() -> LimitsResponse:
    snap = rate_manager.snapshot()
    snap["keys"] = key_pool.snapshot()
    return LimitsResponse(**snap)


@router.get("/quota", response_model=QuotaStatusResponse)
async def quota_status(request: Request, session_id: str | None = None) -> QuotaStatusResponse:
    client_ip = _resolve_client_ip(request)

    keys = [f"ip:{client_ip}"]
    if session_id:
        keys.append(f"session:{session_id}")

    allowed, retry_after, blocked_key, statuses = client_window_limiter.check_multi(keys)

    remaining_effective = min((st["remaining"] for st in statuses.values()), default=settings.chat_window_max_requests)
    blocked_scope = None
    if blocked_key:
        blocked_scope = blocked_key.split(":", 1)[0]

    return QuotaStatusResponse(
        allowed=allowed,
        blocked_scope=blocked_scope,
        retry_after_seconds=retry_after,
        max_requests=settings.chat_window_max_requests,
        window_seconds=settings.chat_window_seconds,
        remaining_effective=remaining_effective,
        scopes=statuses,
    )


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session() -> SessionCreateResponse:
    session_store.cleanup()
    sid = session_store.create()
    return SessionCreateResponse(session_id=sid)


@router.get("/sessions/{session_id}/history")
async def session_history(session_id: str) -> dict:
    turns = session_store.get_turns(session_id)
    if turns == [] and not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "turns": turns}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> Response:
    deleted = session_store.clear(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)


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

    try:
        return await _process_chat(req, session_id, client_ip)
    except HTTPException as exc:
        try:
            chat_logger.log_error(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                status_code=exc.status_code,
                error_type="http_exception",
                error_message=str(exc.detail),
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            chat_logger.log_error(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                status_code=500,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        except Exception:
            pass
        raise


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

    try:
        response = await _process_chat(req, session_id, client_ip)
    except HTTPException as exc:
        try:
            chat_logger.log_error(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                status_code=exc.status_code,
                error_type="http_exception",
                error_message=str(exc.detail),
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            chat_logger.log_error(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                status_code=500,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        except Exception:
            pass
        raise

    async def events():
        payload = {"session_id": response.session_id}
        yield f"event: started\\ndata: {json.dumps(payload)}\\n\\n"

        text = response.answer
        step = 500
        for i in range(0, len(text), step):
            chunk = text[i : i + step]
            yield f"event: token\\ndata: {json.dumps({'text': chunk})}\\n\\n"
            await asyncio.sleep(0)

        yield f"event: completed\\ndata: {json.dumps(response.model_dump())}\\n\\n"

    return StreamingResponse(events(), media_type="text/event-stream")
