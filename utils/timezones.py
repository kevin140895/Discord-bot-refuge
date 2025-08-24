"""Timezone utilities for Machine à sous."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def now_paris() -> datetime:
    """Return current time in Europe/Paris timezone."""
    return datetime.now(PARIS)
