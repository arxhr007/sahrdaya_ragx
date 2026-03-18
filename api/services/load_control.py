import asyncio


class LoadController:
    def __init__(self, max_concurrent: int, queue_wait_seconds: int) -> None:
        self.max_concurrent = max_concurrent
        self.queue_wait_seconds = queue_wait_seconds
        self._sem = asyncio.Semaphore(max_concurrent)
        self._inflight = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.queue_wait_seconds)
            async with self._lock:
                self._inflight += 1
            return True
        except TimeoutError:
            return False

    async def release(self) -> None:
        self._sem.release()
        async with self._lock:
            self._inflight = max(0, self._inflight - 1)

    async def snapshot(self) -> dict:
        async with self._lock:
            return {
                "inflight_requests": self._inflight,
                "max_concurrent": self.max_concurrent,
                "saturated": self._inflight >= self.max_concurrent,
            }
