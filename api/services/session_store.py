import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SessionData:
    created_at: float
    last_accessed: float
    turns: list[dict] = field(default_factory=list)


class SessionStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionData] = {}

    def create(self) -> str:
        sid = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            self._sessions[sid] = SessionData(created_at=now, last_accessed=now)
        return sid

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def get_or_create(self, session_id: str | None) -> str:
        if session_id and self.exists(session_id):
            self.touch(session_id)
            return session_id
        return self.create()

    def touch(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].last_accessed = time.time()

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        now = time.time()
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess.turns.append({"role": role, "content": content, "timestamp": now})
            sess.last_accessed = now

    def get_turns(self, session_id: str) -> list[dict]:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return []
            return list(sess.turns)

    def clear(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                sid
                for sid, data in self._sessions.items()
                if now - data.last_accessed > self._ttl
            ]
            for sid in expired:
                self._sessions.pop(sid, None)
