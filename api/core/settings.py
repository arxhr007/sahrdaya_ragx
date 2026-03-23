from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_cors_origins: str = Field(default="*", alias="API_CORS_ORIGINS")

    groq_model_id: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL_ID")
    groq_api_keys: str = Field(default="", alias="GROQ_API_KEYS")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    session_ttl_seconds: int = Field(default=3600, alias="SESSION_TTL_SECONDS")

    max_concurrent_requests: int = Field(default=4, alias="MAX_CONCURRENT_REQUESTS")
    queue_wait_seconds: int = Field(default=20, alias="QUEUE_WAIT_SECONDS")

    groq_rpm_limit: int = Field(default=30, alias="GROQ_RPM_LIMIT")
    groq_tpm_limit: int = Field(default=8000, alias="GROQ_TPM_LIMIT")
    groq_rpd_limit: int = Field(default=1000, alias="GROQ_RPD_LIMIT")
    groq_tpd_limit: int = Field(default=200000, alias="GROQ_TPD_LIMIT")

    max_continuations: int = Field(default=3, alias="MAX_CONTINUATIONS")

    key_failure_threshold: int = Field(default=2, alias="KEY_FAILURE_THRESHOLD")
    key_default_cooldown_seconds: int = Field(default=15, alias="KEY_DEFAULT_COOLDOWN_SECONDS")

    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    def parsed_keys(self) -> list[str]:
        keys = [k.strip() for k in self.groq_api_keys.split(",") if k.strip()]
        if self.groq_api_key and self.groq_api_key not in keys:
            keys.append(self.groq_api_key)
        return keys


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
