"""Timezone utilities for Machine à sous."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# Central timezone object for Europe/Paris
PARIS_TZ = ZoneInfo("Europe/Paris")
TZ_PARIS = PARIS_TZ  # backward compatibility
PARIS = PARIS_TZ  # backward compatibility


def now_paris() -> datetime:
    """Return current time in Europe/Paris timezone."""
    return datetime.now(PARIS_TZ)
