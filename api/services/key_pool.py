import threading
import time


class KeyPool:
    def __init__(
        self,
        keys: list[str],
        failure_threshold: int = 2,
        default_cooldown_seconds: int = 15,
    ) -> None:
        if not keys:
            raise ValueError("At least one GROQ API key is required")
        self._keys = keys
        self._failure_threshold = failure_threshold
        self._default_cooldown = default_cooldown_seconds
        self._lock = threading.Lock()
        self._cursor = 0
        self._status = {
            k: {"busy_until": 0.0, "failures": 0, "last_error": None}
            for k in keys
        }

    def _now(self) -> float:
        return time.time()

    def acquire(self) -> str | None:
        now = self._now()
        with self._lock:
            n = len(self._keys)
            for _ in range(n):
                key = self._keys[self._cursor % n]
                self._cursor = (self._cursor + 1) % n
                if self._status[key]["busy_until"] <= now:
                    return key
            return None

    def mark_success(self, key: str) -> None:
        with self._lock:
            if key not in self._status:
                return
            self._status[key]["failures"] = 0
            self._status[key]["last_error"] = None

    def mark_busy(self, key: str, cooldown_seconds: int | float | None = None, reason: str | None = None) -> None:
        cooldown = float(cooldown_seconds or self._default_cooldown)
        with self._lock:
            if key not in self._status:
                return
            self._status[key]["busy_until"] = max(self._status[key]["busy_until"], self._now() + cooldown)
            self._status[key]["last_error"] = reason

    def mark_failure(self, key: str, reason: str | None = None) -> None:
        with self._lock:
            if key not in self._status:
                return
            st = self._status[key]
            st["failures"] += 1
            st["last_error"] = reason
            if st["failures"] >= self._failure_threshold:
                st["busy_until"] = max(st["busy_until"], self._now() + self._default_cooldown)

    def snapshot(self) -> list[dict]:
        now = self._now()
        with self._lock:
            out = []
            for k in self._keys:
                st = self._status[k]
                out.append(
                    {
                        "key_hint": self.key_hint(k),
                        "busy": st["busy_until"] > now,
                        "busy_for_seconds": max(0.0, st["busy_until"] - now),
                        "failures": st["failures"],
                        "last_error": st["last_error"],
                    }
                )
            return out

    @staticmethod
    def key_hint(key: str) -> str:
        if len(key) <= 10:
            return "***"
        return f"{key[:4]}...{key[-4:]}"
