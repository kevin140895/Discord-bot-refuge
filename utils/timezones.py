"""Timezone utilities for Machine à sous."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
PARIS = TZ_PARIS  # backward compatibility


def now_paris() -> datetime:
    """Return current time in Europe/Paris timezone."""
    return datetime.now(TZ_PARIS)
