import threading
import time
from collections import deque


class RateLimitManager:
    def __init__(self, rpm: int, tpm: int, rpd: int, tpd: int) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self.rpd = rpd
        self.tpd = tpd

        self._lock = threading.Lock()
        self._minute_requests = deque()
        self._minute_tokens = deque()
        self._day_requests = deque()
        self._day_tokens = deque()

    def _now(self) -> float:
        return time.time()

    def _prune(self, now: float) -> None:
        minute_ago = now - 60
        day_ago = now - 86400

        while self._minute_requests and self._minute_requests[0] <= minute_ago:
            self._minute_requests.popleft()

        while self._minute_tokens and self._minute_tokens[0][0] <= minute_ago:
            self._minute_tokens.popleft()

        while self._day_requests and self._day_requests[0] <= day_ago:
            self._day_requests.popleft()

        while self._day_tokens and self._day_tokens[0][0] <= day_ago:
            self._day_tokens.popleft()

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def can_consume(self, est_tokens: int) -> tuple[bool, float]:
        now = self._now()
        with self._lock:
            self._prune(now)

            minute_req = len(self._minute_requests)
            day_req = len(self._day_requests)
            minute_tok = sum(t for _, t in self._minute_tokens)
            day_tok = sum(t for _, t in self._day_tokens)

            reject = (
                minute_req + 1 > self.rpm
                or day_req + 1 > self.rpd
                or minute_tok + est_tokens > self.tpm
                or day_tok + est_tokens > self.tpd
            )
            if not reject:
                return True, 0.0

            waits = []
            if self._minute_requests:
                waits.append(max(0.0, 60 - (now - self._minute_requests[0])))
            if self._minute_tokens:
                waits.append(max(0.0, 60 - (now - self._minute_tokens[0][0])))
            if self._day_requests:
                waits.append(max(0.0, 86400 - (now - self._day_requests[0])))
            if self._day_tokens:
                waits.append(max(0.0, 86400 - (now - self._day_tokens[0][0])))
            return False, max(waits) if waits else 1.0

    def consume(self, tokens: int) -> None:
        now = self._now()
        with self._lock:
            self._prune(now)
            self._minute_requests.append(now)
            self._day_requests.append(now)
            self._minute_tokens.append((now, max(0, tokens)))
            self._day_tokens.append((now, max(0, tokens)))

    def snapshot(self) -> dict:
        now = self._now()
        with self._lock:
            self._prune(now)
            minute_requests_used = len(self._minute_requests)
            day_requests_used = len(self._day_requests)
            minute_tokens_used = sum(t for _, t in self._minute_tokens)
            day_tokens_used = sum(t for _, t in self._day_tokens)

            reset_minute = 0.0
            if self._minute_requests or self._minute_tokens:
                t0 = min(
                    self._minute_requests[0] if self._minute_requests else now,
                    self._minute_tokens[0][0] if self._minute_tokens else now,
                )
                reset_minute = max(0.0, 60 - (now - t0))

            reset_day = 0.0
            if self._day_requests or self._day_tokens:
                t1 = min(
                    self._day_requests[0] if self._day_requests else now,
                    self._day_tokens[0][0] if self._day_tokens else now,
                )
                reset_day = max(0.0, 86400 - (now - t1))

            return {
                "rpm_limit": self.rpm,
                "tpm_limit": self.tpm,
                "rpd_limit": self.rpd,
                "tpd_limit": self.tpd,
                "minute_requests_used": minute_requests_used,
                "minute_tokens_used": minute_tokens_used,
                "day_requests_used": day_requests_used,
                "day_tokens_used": day_tokens_used,
                "reset_seconds_minute": reset_minute,
                "reset_seconds_day": reset_day,
            }
