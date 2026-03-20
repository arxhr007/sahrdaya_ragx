import json
import logging
import re
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            payload = dict(record.msg)
        else:
            payload = {"message": str(record.getMessage())}

        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        payload.setdefault("level", record.levelname.lower())

        return json.dumps(payload, ensure_ascii=False)


class ChatLogger:
    def __init__(self, logs_dir: str = "logs", max_bytes: int = 5_000_000, backup_count: int = 7) -> None:
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        self._loggers: dict[str, logging.Logger] = {}

    def _sanitize_ip(self, ip: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", (ip or "unknown").strip())
        return safe or "unknown"

    def _get_logger(self, name: str, filename: str) -> logging.Logger:
        with self._lock:
            if name in self._loggers:
                return self._loggers[name]

            logger = logging.getLogger(f"chatlog.{name}")
            logger.setLevel(logging.INFO)
            logger.propagate = False

            if not logger.handlers:
                handler = RotatingFileHandler(
                    self._logs_dir / filename,
                    maxBytes=self._max_bytes,
                    backupCount=self._backup_count,
                    encoding="utf-8",
                )
                handler.setFormatter(JsonLineFormatter())
                logger.addHandler(handler)

            self._loggers[name] = logger
            return logger

    def _emit(self, logger_name: str, filename: str, payload: dict[str, Any]) -> None:
        logger = self._get_logger(logger_name, filename)
        logger.info(payload)

    def log_success(
        self,
        client_ip: str,
        session_id: str,
        question: str,
        answer: str,
        mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "chat_success",
            "client_ip": client_ip,
            "session_id": session_id,
            "mode": mode,
            "question": question,
            "answer": answer,
        }
        if metadata:
            payload["metadata"] = metadata

        ip_safe = self._sanitize_ip(client_ip)
        self._emit("events", "events.jsonl", payload)
        self._emit(f"ip.{ip_safe}", f"{ip_safe}.jsonl", payload)

    def log_error(
        self,
        client_ip: str,
        session_id: str | None,
        question: str,
        status_code: int,
        error_type: str,
        error_message: str,
    ) -> None:
        payload = {
            "event": "chat_error",
            "client_ip": client_ip,
            "session_id": session_id,
            "question": question,
            "status_code": status_code,
            "error_type": error_type,
            "error_message": error_message,
        }

        ip_safe = self._sanitize_ip(client_ip)
        self._emit("events", "events.jsonl", payload)
        self._emit(f"ip.{ip_safe}", f"{ip_safe}.jsonl", payload)
