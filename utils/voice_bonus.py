"""Global state and time windows for temporary voice XP multipliers."""

from __future__ import annotations

from datetime import datetime, timezone

DOUBLE_VOICE_XP_ACTIVE: bool = False
# Completed windows use a real end timestamp. The currently active window keeps
# ``None`` as its end until :func:`set_voice_bonus(False)` closes it.
VOICE_BONUS_WINDOWS: list[tuple[datetime, datetime | None]] = []


def _as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime for comparisons across time zones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def register_voice_bonus_window(start: datetime, end: datetime) -> None:
    """Remember a completed Double XP voice window.

    This is used when a persisted session is restored after a bot restart so a
    member leaving voice later still receives the bonus only for the time that
    really overlapped the session.
    """
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc <= start_utc:
        return
    window = (start_utc, end_utc)
    if window not in VOICE_BONUS_WINDOWS:
        VOICE_BONUS_WINDOWS.append(window)


def set_voice_bonus(active: bool, *, at: datetime | None = None) -> None:
    """Activate or deactivate the global voice XP bonus at ``at``.

    Keeping the transition timestamps lets the XP cog calculate the overlap
    with a voice session instead of applying the state observed at disconnect
    to the whole session retroactively.
    """
    global DOUBLE_VOICE_XP_ACTIVE
    transition = _as_utc(at or datetime.now(timezone.utc))

    if active:
        if not DOUBLE_VOICE_XP_ACTIVE:
            VOICE_BONUS_WINDOWS.append((transition, None))
        DOUBLE_VOICE_XP_ACTIVE = True
        return

    if DOUBLE_VOICE_XP_ACTIVE:
        for index in range(len(VOICE_BONUS_WINDOWS) - 1, -1, -1):
            start, end = VOICE_BONUS_WINDOWS[index]
            if end is None:
                VOICE_BONUS_WINDOWS[index] = (start, max(start, transition))
                break
    DOUBLE_VOICE_XP_ACTIVE = False


def get_voice_bonus_windows(
    start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """Return Double XP windows intersecting ``[start, end)``."""
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc <= start_utc:
        return []

    now = datetime.now(timezone.utc)
    matches: list[tuple[datetime, datetime]] = []
    for window_start, window_end in VOICE_BONUS_WINDOWS:
        effective_end = window_end or now
        overlap_start = max(start_utc, window_start)
        overlap_end = min(end_utc, effective_end)
        if overlap_end > overlap_start:
            matches.append((overlap_start, overlap_end))
    return matches


def get_voice_multiplier(base: float) -> float:
    """Return the instantaneous voice multiplier for compatibility."""
    if DOUBLE_VOICE_XP_ACTIVE and base < 2.0:
        return 2.0
    return base


__all__ = [
    "DOUBLE_VOICE_XP_ACTIVE",
    "VOICE_BONUS_WINDOWS",
    "get_voice_bonus_windows",
    "get_voice_multiplier",
    "register_voice_bonus_window",
    "set_voice_bonus",
]
