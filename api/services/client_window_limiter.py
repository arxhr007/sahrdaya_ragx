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
