from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionCreateResponse(BaseModel):
    session_id: str


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: float


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    session_id: str | None = None
    include_metadata: bool = True


class ChatMetadata(BaseModel):
    mode: Literal["sql", "rag"]
    response_time: float
    prompt_tokens: int
    response_tokens: int
    history_tokens: int
    context_tokens: int
    num_docs: int
    chunk_ids: list[str]
    key_used_hint: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    metadata: ChatMetadata | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str


class LoadResponse(BaseModel):
    inflight_requests: int
    max_concurrent: int
    saturated: bool


class LimitsResponse(BaseModel):
    rpm_limit: int
    tpm_limit: int
    rpd_limit: int
    tpd_limit: int
    minute_requests_used: int
    minute_tokens_used: int
    day_requests_used: int
    day_tokens_used: int
    reset_seconds_minute: float
    reset_seconds_day: float
    keys: list[dict[str, Any]]
