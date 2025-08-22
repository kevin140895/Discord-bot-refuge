"""Global state for temporary voice XP multipliers."""

from __future__ import annotations

DOUBLE_VOICE_XP_ACTIVE: bool = False


def set_voice_bonus(active: bool) -> None:
    """Activate or deactivate the global voice XP bonus."""
    global DOUBLE_VOICE_XP_ACTIVE
    DOUBLE_VOICE_XP_ACTIVE = active


def get_voice_multiplier(base: float) -> float:
    """Return the voice XP multiplier, capped at ×2 when bonus active.

    Parameters
    ----------
    base: float
        Existing multiplier from other mechanics.
    """
    if DOUBLE_VOICE_XP_ACTIVE and base < 2.0:
        return 2.0
    return base

__all__ = ["set_voice_bonus", "get_voice_multiplier"]
