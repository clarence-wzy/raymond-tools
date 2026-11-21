"""FIFO task admission; queued tasks remain cancellable."""

from __future__ import annotations

import threading
from collections import deque


class DownloadScheduler:
    def __init__(self, limit: int = 2):
        self.limit = limit
        self.condition = threading.Condition()
        self.waiting: deque[str] = deque()
        self.active: set[str] = set()

    def configure(self, limit: int) -> None:
        with self.condition:
            self.limit = max(1, min(6, limit))
            self.condition.notify_all()

    def acquire(self, job_id: str, cancelled: threading.Event) -> bool:
        with self.condition:
            self.waiting.append(job_id)
            try:
                while not cancelled.is_set():
                    if self.waiting[0] == job_id and len(self.active) < self.limit:
                        self.waiting.popleft()
                        self.active.add(job_id)
                        self.condition.notify_all()
                        return True
                    self.condition.wait(timeout=0.1)
                return False
            finally:
                if job_id in self.waiting:
                    self.waiting.remove(job_id)
                self.condition.notify_all()

    def release(self, job_id: str) -> None:
        with self.condition:
            self.active.discard(job_id)
            self.condition.notify_all()

    def position(self, job_id: str) -> int | None:
        with self.condition:
            return self.waiting.index(job_id) + 1 if job_id in self.waiting else None


SCHEDULER = DownloadScheduler()
