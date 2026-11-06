"""A controllable clock.

Tests that depend on dates (overdue charges, due-date validation) need time to be
something they can set, not something that happens to them. The clock follows the
wall clock until it is frozen or moved through the control API.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frozen: datetime | None = None
        self._offset = timedelta()

    def now(self) -> datetime:
        with self._lock:
            if self._frozen is not None:
                return self._frozen
            return datetime.now().replace(microsecond=0) + self._offset

    def today(self) -> date:
        return self.now().date()

    def freeze(self, at: datetime) -> None:
        with self._lock:
            self._frozen = at.replace(microsecond=0)
            self._offset = timedelta()

    def advance(self, delta: timedelta) -> None:
        with self._lock:
            if self._frozen is not None:
                self._frozen += delta
            else:
                self._offset += delta

    def reset(self) -> None:
        with self._lock:
            self._frozen = None
            self._offset = timedelta()

    @property
    def frozen(self) -> bool:
        with self._lock:
            return self._frozen is not None
