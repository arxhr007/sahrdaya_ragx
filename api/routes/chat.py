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
    SessionCreateResponse,
)
from api.core.settings import get_settings
from api.services.key_pool import KeyPool
from api.services.chat_logger import ChatLogger
from api.services.load_control import LoadController
from api.services.session_store import SessionStore
from api.services.token_estimator import estimate_tokens
from link_utils import (
    URL_PATTERN,
    extract_urls,
    format_fallback_links,
    harmonize_response_with_links,
    has_useful_link,
    query_likely_needs_links,
)


settings = get_settings()
router = APIRouter(prefix="/api", tags=["chat"])

session_store = SessionStore(ttl_seconds=settings.session_ttl_seconds)
key_pool = KeyPool(
    keys=settings.parsed_keys(),
    failure_threshold=settings.key_failure_threshold,
    default_cooldown_seconds=settings.key_default_cooldown_seconds,
)
load_control = LoadController(
    max_concurrent=settings.max_concurrent_requests,
    queue_wait_seconds=settings.queue_wait_seconds,
)
chat_logger = ChatLogger()



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
    """Mirror rag_setup.classify_and_generate_sql, but async with key failover.

    The stages must match the CLI exactly, or the two front ends answer the same
    question differently: canonicalise, then the student and role fast paths, then
    the bulk gate, and only then the LLM classifier.
    """
    # Canonicalisation can call the LLM, so keep it off the event loop.
    normalized, mapped, expanded = await asyncio.to_thread(
        rag_setup.canonicalize_query_pipeline, question
    )

    direct_student_sql = rag_setup._student_single_lookup_sql(normalized)
    if direct_student_sql:
        return direct_student_sql, ""

    # Role questions ("who is the HOD of CSE") resolve deterministically against the
    # designation column — no LLM, and no chance of a department-wide SELECT.
    role_sql = rag_setup._role_lookup_sql(mapped)
    if role_sql:
        return role_sql, ""

    if not rag_setup._is_bulk_entity_query(expanded):
        return None, ""

    trimmed_history = history_text[-rag_setup._SQL_HISTORY_LIMIT:] if history_text else ""

    async def _runner(key: str):
        llm = ChatGroq(groq_api_key=key, model_name=settings.groq_model_id)
        pv = rag_setup._SQL_CLASSIFY_PROMPT.invoke(
            {
                "schema": rag_setup._FACULTY_SCHEMA,
                "question": expanded,
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


async def _stream_rag_answer(question: str, history_text: str, context_str: str):
    """Yield answer text as the model produces it.

    Key failover only applies before the first token: once bytes are on the wire we
    cannot silently restart on another key, so a mid-stream failure is surfaced.
    """
    base_prompt = rag_setup.prompt.invoke(
        {"question": question, "chat_history": history_text, "context": context_str}
    )
    attempts = max(1, len(key_pool.snapshot()))
    last_error: Exception | None = None

    for _ in range(attempts):
        key = key_pool.acquire()
        if not key:
            break
        llm = ChatGroq(groq_api_key=key, model_name=settings.groq_model_id)
        messages = list(base_prompt.to_messages())
        emitted = False
        try:
            for _ in range(settings.max_continuations + 1):
                part = ""
                finish_reason = None
                async for chunk in llm.astream(messages):
                    text = _extract_text(chunk.content)
                    if text:
                        part += text
                        emitted = True
                        yield text, key
                    finish_reason = (
                        (chunk.response_metadata or {}).get("finish_reason") or finish_reason
                    )
                if finish_reason not in {"length", "max_tokens"}:
                    break
                messages.extend([
                    AIMessage(content=part),
                    HumanMessage(content="Continue exactly from where you stopped. Do not repeat."),
                ])
            key_pool.mark_success(key)
            return
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            if "429" in error_text or "rate limit" in error_text.lower():
                retry_after = _parse_retry_after_seconds(
                    error_text, settings.key_default_cooldown_seconds)
                key_pool.mark_busy(key, cooldown_seconds=retry_after, reason="rate_limited")
            else:
                key_pool.mark_failure(key, reason=error_text[:120])
            if emitted:
                raise HTTPException(
                    status_code=502, detail=f"Stream interrupted: {exc}") from exc

    raise HTTPException(status_code=503, detail=f"No healthy API key available: {last_error}")


def _append_fallback_links(question: str, answer: str, context_str: str) -> str:
    """Attach real document links when the model produced none that point anywhere."""
    if not query_likely_needs_links(question) or has_useful_link(answer):
        return answer
    fallback_urls = extract_urls(context_str, limit=6)
    if not fallback_urls:
        fallback_urls = rag_setup.retrieve_supporting_urls(question, limit=6)
    if not fallback_urls:
        return answer
    answer = answer.rstrip() + "\n\n" + format_fallback_links(question, fallback_urls)
    return harmonize_response_with_links(answer, links_appended=True)


async def _process_chat(req: ChatRequest, session_id: str, client_ip: str = "unknown") -> ChatResponse:
    acquired = await load_control.acquire()
    if not acquired:
        raise HTTPException(status_code=503, detail="Server busy, queue timeout reached")

    t0 = time.time()
    try:
        session_store.cleanup()
        turns = session_store.get_turns(session_id)
        history_text = _build_history_text(turns)

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
            context_str, chunk_ids, num_docs, retrieval_mode = rag_setup.retrieve_with_metadata(
                req.message,
                return_mode=True,
            )
            mode = retrieval_mode
            context_tokens = estimate_tokens(context_str)
            answer, key_used = await _answer_rag(req.message, history_text, context_str)
            answer = _append_fallback_links(req.message, answer, context_str)
            key_hint = KeyPool.key_hint(key_used)

        session_store.append_turn(session_id, "user", req.message)
        session_store.append_turn(session_id, "assistant", answer)

        prompt_tokens = estimate_tokens(req.message)
        response_tokens = estimate_tokens(answer)
        history_tokens = estimate_tokens(history_text)

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
    """Report what actually throttles traffic, and what is merely configured.

    The GROQ_* limits are advisory: this app does not meter requests or tokens
    against them. Enforcement is concurrency, reactive 429 handling, and Nginx.
    """
    load_snap = await load_control.snapshot()
    return LimitsResponse(
        enforcement=[
            f"load_control: at most {settings.max_concurrent_requests} concurrent chat "
            f"requests, {settings.queue_wait_seconds}s queue wait then 503",
            "key_pool: reactive cooldown + failover when Groq returns 429",
            "nginx: per-IP limit_req on /api/chat and /api/chat/stream "
            "(nginx deployment only)",
        ],
        advisory_groq_limits={
            "rpm": settings.groq_rpm_limit,
            "tpm": settings.groq_tpm_limit,
            "rpd": settings.groq_rpd_limit,
            "tpd": settings.groq_tpd_limit,
        },
        inflight_requests=load_snap["inflight_requests"],
        max_concurrent=load_snap["max_concurrent"],
        saturated=load_snap["saturated"],
        keys=key_pool.snapshot(),
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
    """Server-sent events: started -> token* -> completed.

    Tokens are emitted as the model generates them. Previously the full answer was
    computed first and then sliced into 500-char pieces, so the client waited the entire
    generation time before seeing anything.
    """
    session_store.cleanup()
    session_id = session_store.get_or_create(req.session_id)
    client_ip = _resolve_client_ip(request)

    def _sse(event: str, payload: dict) -> str:
        return "event: %s\ndata: %s\n\n" % (event, json.dumps(payload))

    def _log_error(status_code: int, error_type: str, message: str) -> None:
        try:
            chat_logger.log_error(
                client_ip=client_ip,
                session_id=session_id,
                question=req.message,
                status_code=status_code,
                error_type=error_type,
                error_message=message,
            )
        except Exception:
            pass

    acquired = await load_control.acquire()
    if not acquired:
        _log_error(503, "http_exception", "Server busy, queue timeout reached")
        raise HTTPException(status_code=503, detail="Server busy, queue timeout reached")

    async def events():
        t0 = time.time()
        answer = ""
        mode = "rag"
        chunk_ids: list[str] = []
        num_docs = 0
        context_tokens = 0
        context_str = ""
        key_hint = None
        try:
            yield _sse("started", {"session_id": session_id})

            turns = session_store.get_turns(session_id)
            history_text = _build_history_text(turns)

            sql_query, key_for_sql = await _classify_sql(req.message, history_text)
            key_hint = KeyPool.key_hint(key_for_sql)

            if sql_query and rag_setup.validate_faculty_sql(sql_query):
                sql_result = rag_setup.execute_faculty_sql(sql_query)
                if sql_result:
                    cols, rows = sql_result
                    if rows:
                        answer = rag_setup.format_sql_results(cols, rows, req.message)
                        mode = "sql"

            if answer:
                # SQL answers are already complete; send as one token event.
                yield _sse("token", {"text": answer})
            else:
                context_str, chunk_ids, num_docs, mode = rag_setup.retrieve_with_metadata(
                    req.message, return_mode=True,
                )
                context_tokens = estimate_tokens(context_str)
                used_key = None
                async for delta, used_key in _stream_rag_answer(
                    req.message, history_text, context_str
                ):
                    answer += delta
                    yield _sse("token", {"text": delta})
                if used_key:
                    key_hint = KeyPool.key_hint(used_key)

                # Link fallback can only run once the whole answer is known, so any
                # appended links arrive as a final token event before completion.
                enriched = _append_fallback_links(req.message, answer, context_str)
                if enriched != answer:
                    yield _sse("token", {"text": enriched[len(answer):]})
                    answer = enriched

            session_store.append_turn(session_id, "user", req.message)
            session_store.append_turn(session_id, "assistant", answer)

            metadata = {
                "mode": mode,
                "response_time": time.time() - t0,
                "prompt_tokens": estimate_tokens(req.message),
                "response_tokens": estimate_tokens(answer),
                "history_tokens": estimate_tokens(history_text),
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
                pass

            response = ChatResponse(
                session_id=session_id,
                answer=answer,
                metadata=metadata if req.include_metadata else None,
            )
            yield _sse("completed", response.model_dump())
        except HTTPException as exc:
            _log_error(exc.status_code, "http_exception", str(exc.detail))
            yield _sse("error", {"status": exc.status_code, "detail": str(exc.detail)})
        except Exception as exc:
            _log_error(500, exc.__class__.__name__, str(exc))
            yield _sse("error", {"status": 500, "detail": str(exc)})
        finally:
            await load_control.release()

    return StreamingResponse(events(), media_type="text/event-stream")
