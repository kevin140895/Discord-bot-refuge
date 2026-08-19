from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from storage.roulette_reaction_store import (
    CASINO_ACTIVE_BETS_MIN,
    CASINO_BUSY_BETS_MIN,
    CASINO_BUSY_PLAYERS_MIN,
    CASINO_STRONG_STREAK_MAX_IDLE_MINUTES,
    CASINO_STRONG_STREAK_MIN,
    RouletteReactionStore,
    roulette_reaction_store,
)


CasinoActivityLevel = Literal["calm", "active", "busy"]
CasinoReactionKind = Literal[
    "none",
    "green_zero",
    "royal_win",
    "players_streak",
    "house_streak",
]

CASINO_REACTION_LABELS: Final[dict[str, str]] = {
    "none": "Calme",
    "green_zero": "Éclat du Zéro Vert",
    "royal_win": "Célébration royale",
    "players_streak": "Joueurs en feu",
    "house_streak": "La Maison domine",
}
CASINO_ACTIVITY_LABELS: Final[dict[str, str]] = {
    "calm": "Calme",
    "active": "Tables actives",
    "busy": "Forte affluence",
}
CASINO_REACTION_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        "normal",
        "active",
        "busy",
        "green_zero",
        "royal_win",
        "players_streak",
        "house_streak",
    }
)


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CasinoReactionState:
    """Discrete visual-only reaction state derived from already-recorded bets."""

    activity: CasinoActivityLevel = "calm"
    reaction: CasinoReactionKind = "none"
    bets_10m: int = 0
    unique_players_10m: int = 0
    streak_count: int = 0

    @property
    def cache_key(self) -> str:
        # Deliberately excludes exact counts so high traffic does not render a new
        # PNG for every single bet. Only discrete visual state changes matter.
        return f"{self.activity}-{self.reaction}"

    @property
    def label(self) -> str:
        if self.reaction != "none":
            return CASINO_REACTION_LABELS[self.reaction]
        return CASINO_ACTIVITY_LABELS[self.activity]

    @property
    def is_notable(self) -> bool:
        return self.activity != "calm" or self.reaction != "none"


NORMAL_CASINO_REACTION: Final[CasinoReactionState] = CasinoReactionState()


def casino_reaction_override(value: str | None) -> CasinoReactionState:
    normalized = "normal" if value is None else str(value).strip().lower()
    if normalized not in CASINO_REACTION_OVERRIDES:
        raise ValueError(f"unsupported Casino reaction override: {normalized}")
    if normalized == "normal":
        return NORMAL_CASINO_REACTION
    if normalized == "active":
        return CasinoReactionState(activity="active")
    if normalized == "busy":
        return CasinoReactionState(activity="busy", bets_10m=8, unique_players_10m=4)
    if normalized == "green_zero":
        return CasinoReactionState(activity="active", reaction="green_zero")
    if normalized == "royal_win":
        return CasinoReactionState(activity="active", reaction="royal_win")
    if normalized == "players_streak":
        return CasinoReactionState(
            activity="active", reaction="players_streak", streak_count=5
        )
    return CasinoReactionState(
        activity="active", reaction="house_streak", streak_count=5
    )


def build_casino_reaction_state(
    snapshot: dict[str, Any],
    *,
    at: datetime | None = None,
) -> CasinoReactionState:
    now = _aware_utc(at)
    bets = max(0, int(snapshot.get("bets_10m", 0) or 0))
    players = max(0, int(snapshot.get("unique_players_10m", 0) or 0))
    if bets >= CASINO_BUSY_BETS_MIN or players >= CASINO_BUSY_PLAYERS_MIN:
        activity: CasinoActivityLevel = "busy"
    elif bets >= CASINO_ACTIVE_BETS_MIN:
        activity = "active"
    else:
        activity = "calm"

    # Exceptional reactions outrank normal activity. When two exceptional
    # events overlap, the most recently observed event wins deterministically.
    exceptional: list[tuple[datetime, CasinoReactionKind]] = []
    zero_at = _parse_utc(snapshot.get("latest_zero_at"))
    if zero_at is not None:
        exceptional.append((zero_at, "green_zero"))
    big_win_at = _parse_utc(snapshot.get("latest_big_win_at"))
    if big_win_at is not None:
        exceptional.append((big_win_at, "royal_win"))
    if exceptional:
        exceptional.sort(key=lambda item: (item[0], item[1] == "green_zero"), reverse=True)
        reaction = exceptional[0][1]
        return CasinoReactionState(
            activity=activity,
            reaction=reaction,
            bets_10m=bets,
            unique_players_10m=players,
        )

    streak_count = max(0, int(snapshot.get("streak_count", 0) or 0))
    streak_side = str(snapshot.get("streak_side") or "")
    streak_at = _parse_utc(snapshot.get("streak_at"))
    streak_is_fresh = (
        streak_at is not None
        and now - streak_at <= timedelta(minutes=CASINO_STRONG_STREAK_MAX_IDLE_MINUTES)
    )
    reaction: CasinoReactionKind = "none"
    if streak_count >= CASINO_STRONG_STREAK_MIN and streak_is_fresh:
        if streak_side == "players":
            reaction = "players_streak"
        elif streak_side == "house":
            reaction = "house_streak"

    return CasinoReactionState(
        activity=activity,
        reaction=reaction,
        bets_10m=bets,
        unique_players_10m=players,
        streak_count=streak_count if reaction != "none" else 0,
    )


class CasinoReactionService:
    def __init__(self, store: RouletteReactionStore = roulette_reaction_store) -> None:
        self.store = store

    async def evaluate(self, *, at: datetime | None = None) -> CasinoReactionState:
        snapshot = await self.store.get_snapshot(at=at)
        return build_casino_reaction_state(snapshot, at=at)


casino_reaction_service = CasinoReactionService()


__all__ = [
    "CASINO_ACTIVITY_LABELS",
    "CASINO_REACTION_LABELS",
    "CASINO_REACTION_OVERRIDES",
    "CasinoActivityLevel",
    "CasinoReactionKind",
    "CasinoReactionService",
    "CasinoReactionState",
    "NORMAL_CASINO_REACTION",
    "build_casino_reaction_state",
    "casino_reaction_override",
    "casino_reaction_service",
]
