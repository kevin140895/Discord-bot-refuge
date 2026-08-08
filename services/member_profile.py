from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from config import DATA_DIR
from storage.achievement_store import achievement_store
from storage.season_store import season_store
from storage.xp_store import xp_store
from utils.achievements import ACHIEVEMENTS
from utils.persistence import read_json_safe
from utils.seasons import parse_season_id, rank_rows, season_id_for


CASINO_STATE_FILE = Path(DATA_DIR) / "pari_xp_state.json"
_KNOWN_ACHIEVEMENT_IDS = frozenset(item.id for item in ACHIEVEMENTS)


class XPReader(Protocol):
    async def get_user_data(self, user_id: int) -> Mapping[str, Any]: ...


class AchievementReader(Protocol):
    async def get_user_achievements(self, user_id: int) -> Mapping[str, str]: ...


class SeasonReader(Protocol):
    async def get_season(self, season_id: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class MemberProfileSnapshot:
    """Read-only aggregate consumed by future Discord profile views."""

    user_id: int
    xp: int
    level: int
    achievements_unlocked: int
    achievements_total: int
    achievement_ids: tuple[str, ...]
    season_id: str
    season_xp: int
    season_xp_rank: int | None
    season_messages: int
    season_messages_rank: int | None
    season_voice_seconds: int
    season_voice_rank: int | None
    season_casino_net: int
    season_casino_rank: int | None
    casino_bets: int
    casino_wagered: int
    casino_winnings: int
    casino_net: int


def _safe_int(value: Any, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _casino_player_payload(raw: Any, user_id: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    players = raw.get("players", {})
    if not isinstance(players, Mapping):
        return {}
    payload = players.get(str(user_id), {})
    return dict(payload) if isinstance(payload, Mapping) else {}


def _season_users(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    users = payload.get("users", {})
    if not isinstance(users, Mapping):
        return {}
    return {
        str(user_id): dict(user_payload)
        for user_id, user_payload in users.items()
        if isinstance(user_payload, Mapping)
    }


def _rank_for_user(
    users: dict[str, dict[str, Any]],
    field: str,
    user_id: int,
) -> int | None:
    target = str(user_id)
    for rank, (row_user_id, _value) in enumerate(rank_rows(users, field), start=1):
        if row_user_id == target:
            return rank
    return None


def build_member_profile_snapshot(
    *,
    user_id: int,
    season_id: str,
    xp_payload: Mapping[str, Any] | None,
    unlocked_achievements: Mapping[str, str] | None,
    season_payload: Mapping[str, Any] | None,
    casino_payload: Mapping[str, Any] | None,
) -> MemberProfileSnapshot:
    """Combine authoritative source snapshots without mutating any store."""

    parse_season_id(season_id)
    xp_payload = xp_payload if isinstance(xp_payload, Mapping) else {}
    unlocked_achievements = (
        unlocked_achievements if isinstance(unlocked_achievements, Mapping) else {}
    )
    casino_payload = casino_payload if isinstance(casino_payload, Mapping) else {}

    known_unlocked = tuple(
        sorted(
            achievement_id
            for achievement_id in unlocked_achievements
            if achievement_id in _KNOWN_ACHIEVEMENT_IDS
        )
    )

    users = _season_users(season_payload)
    season_user = users.get(str(user_id), {})

    casino_bets = _safe_int(casino_payload.get("bets", 0), minimum=0)
    casino_wagered = _safe_int(casino_payload.get("wagered", 0), minimum=0)
    casino_winnings = _safe_int(casino_payload.get("winnings", 0), minimum=0)

    return MemberProfileSnapshot(
        user_id=int(user_id),
        xp=_safe_int(xp_payload.get("xp", 0), minimum=0),
        level=_safe_int(xp_payload.get("level", 0), minimum=0),
        achievements_unlocked=len(known_unlocked),
        achievements_total=len(ACHIEVEMENTS),
        achievement_ids=known_unlocked,
        season_id=season_id,
        season_xp=_safe_int(season_user.get("xp_earned", 0), minimum=0),
        season_xp_rank=_rank_for_user(users, "xp_earned", user_id),
        season_messages=_safe_int(season_user.get("messages", 0), minimum=0),
        season_messages_rank=_rank_for_user(users, "messages", user_id),
        season_voice_seconds=_safe_int(
            season_user.get("voice_seconds", 0), minimum=0
        ),
        season_voice_rank=_rank_for_user(users, "voice_seconds", user_id),
        season_casino_net=_safe_int(season_user.get("casino_net", 0)),
        season_casino_rank=_rank_for_user(users, "casino_net", user_id),
        casino_bets=casino_bets,
        casino_wagered=casino_wagered,
        casino_winnings=casino_winnings,
        casino_net=casino_winnings - casino_wagered,
    )


class MemberProfileService:
    """Read existing stores and expose one consistent member profile snapshot."""

    def __init__(
        self,
        *,
        xp_reader: XPReader = xp_store,
        achievement_reader: AchievementReader = achievement_store,
        season_reader: SeasonReader = season_store,
        casino_state_file: str | Path = CASINO_STATE_FILE,
    ) -> None:
        self._xp_reader = xp_reader
        self._achievement_reader = achievement_reader
        self._season_reader = season_reader
        self._casino_state_file = Path(casino_state_file)

    async def get_snapshot(
        self,
        user_id: int,
        *,
        season_id: str | None = None,
    ) -> MemberProfileSnapshot:
        resolved_season_id = season_id or season_id_for()
        parse_season_id(resolved_season_id)

        xp_payload, unlocked, season_payload = await asyncio.gather(
            self._xp_reader.get_user_data(user_id),
            self._achievement_reader.get_user_achievements(user_id),
            self._season_reader.get_season(resolved_season_id),
        )
        raw_casino = await asyncio.to_thread(
            read_json_safe,
            self._casino_state_file,
            {},
        )
        casino_payload = _casino_player_payload(raw_casino, user_id)

        return build_member_profile_snapshot(
            user_id=user_id,
            season_id=resolved_season_id,
            xp_payload=xp_payload,
            unlocked_achievements=unlocked,
            season_payload=season_payload,
            casino_payload=casino_payload,
        )


member_profile_service = MemberProfileService()


__all__ = [
    "CASINO_STATE_FILE",
    "MemberProfileService",
    "MemberProfileSnapshot",
    "build_member_profile_snapshot",
    "member_profile_service",
]
