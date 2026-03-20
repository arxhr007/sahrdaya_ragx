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

    def _status_for_queue(self, q: deque[float], now: float) -> dict:
        used = len(q)
        remaining = max(0, self.max_requests - used)
        retry_after = 0
        reset_after = 0

        if q:
            reset_after = int(max(1, self.window_seconds - (now - q[0])))
        if used >= self.max_requests:
            retry_after = int(max(1, self.window_seconds - (now - q[0])))

        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "used": used,
            "remaining": remaining,
            "retry_after_seconds": retry_after,
            "reset_after_seconds": reset_after,
            "blocked": used >= self.max_requests,
        }

    def check(self, key: str) -> dict:
        now = self._now()
        with self._lock:
            q = self._prune(now, key)
            return self._status_for_queue(q, now)

    def check_multi(self, keys: list[str]) -> tuple[bool, int, str | None, dict[str, dict]]:
        now = self._now()
        unique_keys = [k for k in dict.fromkeys(keys) if k]
        if not unique_keys:
            unique_keys = ["ip:unknown"]

        with self._lock:
            statuses: dict[str, dict] = {}
            blocked_key = None
            retry_after = 0

            for key in unique_keys:
                q = self._prune(now, key)
                st = self._status_for_queue(q, now)
                statuses[key] = st
                if st["blocked"] and blocked_key is None:
                    blocked_key = key
                    retry_after = st["retry_after_seconds"]

            allowed = blocked_key is None
            return allowed, retry_after, blocked_key, statuses
