"""Relógio injetável: o domínio nunca chama datetime.now() diretamente."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:  # sempre timezone-aware em UTC
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Relógio controlado por testes. Compartilhável entre threads."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock exige datetime com fuso")
        self._now = start.astimezone(timezone.utc)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock exige datetime com fuso")
        with self._lock:
            self._now = value.astimezone(timezone.utc)

    def advance(self, **kwargs: float) -> datetime:
        with self._lock:
            self._now = self._now + timedelta(**kwargs)
            return self._now


def utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Valores lidos do MySQL (DATETIME) são gravados sempre em UTC.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
