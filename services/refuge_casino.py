from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from config import CASINO_CLOSE_HOUR, CASINO_OPEN_HOUR, DATA_DIR
from models.refuge_world import (
    RefugeBuildingState,
    RefugeHistoricalEvent,
    RefugeWorldState,
)
from services.refuge_world import (
    BuildingProgressionRule,
    RefugeWorldService,
    refuge_world_service,
    world_render_signature,
)
from storage.refuge_casino_activity_store import (
    RefugeCasinoActivityStore,
    refuge_casino_activity_store,
)
from utils.persistence import read_json_safe
from utils.timezones import PARIS_TZ


CASINO_BUILDING_ID: Final[str] = "casino"
CASINO_METRIC_KEY: Final[str] = "casino_prestige_points"
CASINO_MAX_LEVEL: Final[int] = 5
CASINO_RECENT_WINDOW_SECONDS: Final[int] = 24 * 60 * 60
CASINO_STATE_FILE = Path(DATA_DIR) / "pari_xp_state.json"

CASINO_LEVEL_NAMES: Final[Mapping[int, str]] = {
    1: "Baraque de Jeux",
    2: "Comptoir Chanceux",
    3: "Casino du Refuge",
    4: "Palais du Hasard",
    5: "Maison Éternelle",
}
CASINO_FORTUNE_NAMES: Final[Mapping[str, str]] = {
    "ruined": "Ruiné",
    "difficulty": "En difficulté",
    "stable": "Stable",
    "prosperous": "Prospère",
    "insolent": "Insolent",
}
CASINO_EVENTS: Final[Mapping[str, str]] = {
    "grand_heist": "Le Grand Braquage",
    "black_night": "La Nuit Noire",
    "break_in": "Le Casse",
    "house_always_wins": "La Maison gagne toujours",
}
CASINO_SECRET_EVENTS: Final[Mapping[str, str]] = {
    "black_cat": "Le Chat Noir",
    "diamond": "Le Diamant",
    "ghost_player": "Le Joueur Fantôme",
}


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _csv_ints(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    values: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"{name} contains an empty value")
        try:
            values.append(int(token))
        except ValueError as exc:
            raise ValueError(f"{name} must contain integers") from exc
    return tuple(values)


def _env_nonnegative_int(name: str) -> int:
    raw = os.getenv(name, "0").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True, slots=True)
class RefugeCasinoConfig:
    level_thresholds_points: tuple[int, ...] = ()
    roulette_bet_weight: int = 0
    roulette_player_weight: int = 0
    jackpot_500_weight: int = 0
    jackpot_1000_weight: int = 0
    fortune_thresholds_xp: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.level_thresholds_points and len(self.level_thresholds_points) != 4:
            raise ValueError("casino level thresholds must contain exactly 4 values")
        previous = -1
        for value in self.level_thresholds_points:
            if int(value) <= 0 or int(value) <= previous:
                raise ValueError(
                    "casino level thresholds must be positive and strictly increasing"
                )
            previous = int(value)
        for value in (
            self.roulette_bet_weight,
            self.roulette_player_weight,
            self.jackpot_500_weight,
            self.jackpot_1000_weight,
        ):
            if int(value) < 0:
                raise ValueError("casino prestige weights must be >= 0")
        if self.fortune_thresholds_xp and len(self.fortune_thresholds_xp) != 4:
            raise ValueError("casino fortune thresholds must contain exactly 4 values")
        previous_fortune: int | None = None
        for value in self.fortune_thresholds_xp:
            current = int(value)
            if previous_fortune is not None and current <= previous_fortune:
                raise ValueError("casino fortune thresholds must be strictly increasing")
            previous_fortune = current

    @classmethod
    def from_env(cls) -> "RefugeCasinoConfig":
        return cls(
            level_thresholds_points=_csv_ints(
                "REFUGE_CASINO_LEVEL_THRESHOLDS_POINTS"
            ),
            roulette_bet_weight=_env_nonnegative_int(
                "REFUGE_CASINO_ROULETTE_BET_WEIGHT"
            ),
            roulette_player_weight=_env_nonnegative_int(
                "REFUGE_CASINO_ROULETTE_PLAYER_WEIGHT"
            ),
            jackpot_500_weight=_env_nonnegative_int(
                "REFUGE_CASINO_JACKPOT_500_WEIGHT"
            ),
            jackpot_1000_weight=_env_nonnegative_int(
                "REFUGE_CASINO_JACKPOT_1000_WEIGHT"
            ),
            fortune_thresholds_xp=_csv_ints(
                "REFUGE_CASINO_FORTUNE_THRESHOLDS_XP"
            ),
        )


@dataclass(frozen=True, slots=True)
class CasinoSourceSnapshot:
    roulette_bet_count: int = 0
    roulette_unique_players: int = 0
    roulette_wagered_xp: int = 0
    roulette_winnings_xp: int = 0

    @property
    def roulette_house_net_xp(self) -> int:
        return self.roulette_wagered_xp - self.roulette_winnings_xp


@dataclass(frozen=True, slots=True)
class RefugeCasinoStatus:
    state: RefugeWorldState
    level: int
    level_name: str
    prestige_points: int
    fortune: str
    fortune_name: str
    is_open: bool
    roulette_bet_count: int
    roulette_unique_players: int
    roulette_lifetime_house_net_xp: int
    recent_house_net_xp: int
    recent_transactions: int
    tracking_started_at: str | None
    last_jackpot: Mapping[str, Any] | None
    changed: bool
    render_signature: str


def casino_level_name(level: int) -> str:
    normalized = max(1, min(CASINO_MAX_LEVEL, int(level)))
    return CASINO_LEVEL_NAMES[normalized]


def casino_is_open(at: datetime | None = None) -> bool:
    current = at or datetime.now(PARIS_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=PARIS_TZ)
    local = current.astimezone(PARIS_TZ)
    hour = local.hour
    if CASINO_OPEN_HOUR < CASINO_CLOSE_HOUR:
        return CASINO_OPEN_HOUR <= hour < CASINO_CLOSE_HOUR
    return hour >= CASINO_OPEN_HOUR or hour < CASINO_CLOSE_HOUR


def casino_fortune_for_net(
    net_xp: int,
    *,
    transactions: int,
    thresholds: tuple[int, ...] = (),
) -> str:
    net = int(net_xp)
    if thresholds:
        if len(thresholds) != 4:
            raise ValueError("casino fortune thresholds must contain 4 values")
        a, b, c, d = (int(value) for value in thresholds)
        if net < a:
            return "ruined"
        if net < b:
            return "difficulty"
        if net < c:
            return "stable"
        if net < d:
            return "prosperous"
        return "insolent"

    # Without calibrated magnitude thresholds, only the sign of an actually
    # observed recent net can be interpreted safely. Extreme states remain
    # unavailable until explicit thresholds are configured.
    if int(transactions) <= 0 or net == 0:
        return "stable"
    return "prosperous" if net > 0 else "difficulty"


def casino_source_snapshot(raw: Any) -> CasinoSourceSnapshot:
    if not isinstance(raw, dict):
        return CasinoSourceSnapshot()
    players = raw.get("players", {})
    if not isinstance(players, dict):
        players = {}
    bet_count = 0
    unique_players = 0
    for payload in players.values():
        if not isinstance(payload, dict):
            continue
        try:
            bets = max(0, int(payload.get("bets", 0)))
        except (TypeError, ValueError):
            bets = 0
        bet_count += bets
        if bets > 0:
            unique_players += 1
    try:
        wagered = max(0, int(raw.get("total_bets", 0)))
    except (TypeError, ValueError):
        wagered = 0
    try:
        winnings = max(0, int(raw.get("total_winnings", 0)))
    except (TypeError, ValueError):
        winnings = 0
    return CasinoSourceSnapshot(
        roulette_bet_count=bet_count,
        roulette_unique_players=unique_players,
        roulette_wagered_xp=wagered,
        roulette_winnings_xp=winnings,
    )


def casino_prestige_points(
    source: CasinoSourceSnapshot,
    activity_snapshot: Mapping[str, Any],
    config: RefugeCasinoConfig,
) -> int:
    totals = activity_snapshot.get("totals", {})
    if not isinstance(totals, Mapping):
        totals = {}
    try:
        jackpot_500 = max(0, int(totals.get("jackpots_500", 0)))
        jackpot_1000 = max(0, int(totals.get("jackpots_1000", 0)))
    except (TypeError, ValueError):
        jackpot_500 = 0
        jackpot_1000 = 0
    return (
        source.roulette_bet_count * int(config.roulette_bet_weight)
        + source.roulette_unique_players * int(config.roulette_player_weight)
        + jackpot_500 * int(config.jackpot_500_weight)
        + jackpot_1000 * int(config.jackpot_1000_weight)
    )


def _casino_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == CASINO_BUILDING_ID
        ),
        None,
    )


def _replace_building(
    state: RefugeWorldState,
    building: RefugeBuildingState,
) -> RefugeWorldState:
    buildings = {item.building_id: item for item in state.buildings}
    buildings[building.building_id] = building
    return replace(
        state,
        buildings=tuple(sorted(buildings.values(), key=lambda item: item.building_id)),
    )


def _string_list(value: Any, *, allowed: Mapping[str, str]) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({str(item) for item in value if str(item) in allowed})


class RefugeCasinoService:
    """Project existing casino evidence into permanent Refuge world state."""

    def __init__(
        self,
        *,
        activity_store: RefugeCasinoActivityStore = refuge_casino_activity_store,
        world_service: RefugeWorldService = refuge_world_service,
        state_file: str | Path = CASINO_STATE_FILE,
    ) -> None:
        self.activity_store = activity_store
        self.world_service = world_service
        self.world_store = world_service.store
        self.state_file = Path(state_file)
        self._lock = asyncio.Lock()

    async def _source_snapshot(self) -> CasinoSourceSnapshot:
        raw = await asyncio.to_thread(read_json_safe, self.state_file, {})
        return casino_source_snapshot(raw)

    async def evaluate(
        self,
        *,
        config: RefugeCasinoConfig | None = None,
        at: datetime | None = None,
    ) -> RefugeCasinoStatus:
        casino_config = config or RefugeCasinoConfig.from_env()
        async with self._lock:
            return await self._evaluate_locked(config=casino_config, at=at)

    async def _evaluate_locked(
        self,
        *,
        config: RefugeCasinoConfig,
        at: datetime | None,
    ) -> RefugeCasinoStatus:
        source = await self._source_snapshot()
        activity = await self.activity_store.initialize(at=at)
        recent = await self.activity_store.get_recent_totals(
            window_seconds=CASINO_RECENT_WINDOW_SECONDS,
            at=at,
        )
        prestige = casino_prestige_points(source, activity, config)
        progression = await self.world_service.evaluate(
            metrics={CASINO_METRIC_KEY: prestige},
            rules=(
                BuildingProgressionRule(
                    building_id=CASINO_BUILDING_ID,
                    metric_key=CASINO_METRIC_KEY,
                    thresholds=config.level_thresholds_points,
                    minimum_level=1,
                    event_from_level=2,
                ),
            ),
            at=at,
        )
        state = progression.state
        building = _casino_building(state)
        if building is None:
            raise RuntimeError("Refuge Casino progression did not create a building")

        recent_net = (
            int(recent.get("roulette_wagered_xp", 0))
            - int(recent.get("roulette_payout_xp", 0))
            - int(recent.get("machine_payout_xp", 0))
        )
        recent_transactions = max(0, int(recent.get("transactions", 0)))
        fortune = casino_fortune_for_net(
            recent_net,
            transactions=recent_transactions,
            thresholds=config.fortune_thresholds_xp,
        )
        open_now = casino_is_open(at)

        events = list(state.events)
        event_ids = {event.event_id for event in events}
        raw_jackpots = activity.get("jackpots", [])
        jackpots = [dict(item) for item in raw_jackpots if isinstance(item, dict)]
        jackpots.sort(
            key=lambda item: (
                str(item.get("occurred_at", "")),
                str(item.get("event_id", "")),
            )
        )
        imported_jackpot = False
        for item in jackpots:
            source_event_id = str(item.get("event_id", "")).strip()
            if not source_event_id:
                continue
            event_id = f"casino:jackpot:{source_event_id}"
            if event_id in event_ids:
                continue
            try:
                tier = int(item.get("tier", 0))
                user_id = int(item.get("user_id", 0))
                applied_xp = max(0, int(item.get("applied_xp", 0)))
            except (TypeError, ValueError):
                continue
            if tier not in {500, 1000}:
                continue
            events.append(
                RefugeHistoricalEvent(
                    event_id=event_id,
                    event_type="casino_jackpot_observed",
                    occurred_at=str(item.get("occurred_at") or _utc_iso(at)),
                    data={
                        "building_id": CASINO_BUILDING_ID,
                        "tier": tier,
                        "user_id": user_id,
                        "applied_xp": applied_xp,
                    },
                )
            )
            event_ids.add(event_id)
            imported_jackpot = True

        building_state = dict(building.state)
        desired = {
            "fortune": fortune,
            "is_open": open_now,
            "casino_events": _string_list(
                building_state.get("casino_events", ()),
                allowed=CASINO_EVENTS,
            ),
            "secret_events": _string_list(
                building_state.get("secret_events", ()),
                allowed=CASINO_SECRET_EVENTS,
            ),
        }
        last_jackpot: dict[str, Any] | None = jackpots[-1] if jackpots else None
        if last_jackpot is not None:
            try:
                desired["last_jackpot"] = {
                    "tier": int(last_jackpot.get("tier", 0)),
                    "occurred_at": str(last_jackpot.get("occurred_at", "")),
                }
            except (TypeError, ValueError):
                last_jackpot = None
        elif isinstance(building_state.get("last_jackpot"), Mapping):
            desired["last_jackpot"] = dict(building_state["last_jackpot"])

        state_changed = progression.changed or imported_jackpot
        if any(building_state.get(key) != value for key, value in desired.items()):
            building_state.update(desired)
            building = replace(building, state=building_state)
            state = _replace_building(state, building)
            state_changed = True
        if imported_jackpot:
            state = replace(state, events=tuple(events))

        if state_changed:
            state = await self.world_store.save_state(state)

        tracking_started = activity.get("tracking_started_at")
        return RefugeCasinoStatus(
            state=state,
            level=max(1, min(CASINO_MAX_LEVEL, int(building.level))),
            level_name=casino_level_name(building.level),
            prestige_points=prestige,
            fortune=fortune,
            fortune_name=CASINO_FORTUNE_NAMES[fortune],
            is_open=open_now,
            roulette_bet_count=source.roulette_bet_count,
            roulette_unique_players=source.roulette_unique_players,
            roulette_lifetime_house_net_xp=source.roulette_house_net_xp,
            recent_house_net_xp=recent_net,
            recent_transactions=recent_transactions,
            tracking_started_at=str(tracking_started) if tracking_started else None,
            last_jackpot=last_jackpot,
            changed=state_changed,
            render_signature=world_render_signature(state),
        )

    async def _unlock_marker(
        self,
        marker_id: str,
        *,
        mapping: Mapping[str, str],
        state_key: str,
        event_type: str,
        at: datetime | None,
        config: RefugeCasinoConfig | None,
    ) -> RefugeWorldState:
        normalized = str(marker_id).strip()
        if normalized not in mapping:
            raise ValueError(f"unsupported Casino marker: {normalized}")
        casino_config = config or RefugeCasinoConfig.from_env()
        async with self._lock:
            status = await self._evaluate_locked(config=casino_config, at=at)
            state = status.state
            building = _casino_building(state)
            if building is None:
                raise RuntimeError("Refuge Casino building is missing")
            event_id = f"casino:{state_key}:{normalized}"
            if any(event.event_id == event_id for event in state.events):
                return state

            building_state = dict(building.state)
            values = set(
                _string_list(building_state.get(state_key, ()), allowed=mapping)
            )
            values.add(normalized)
            building_state[state_key] = sorted(values)
            updated = _replace_building(
                state,
                replace(building, state=building_state),
            )
            updated = replace(
                updated,
                events=updated.events
                + (
                    RefugeHistoricalEvent(
                        event_id=event_id,
                        event_type=event_type,
                        occurred_at=_utc_iso(at),
                        data={
                            "building_id": CASINO_BUILDING_ID,
                            "marker_id": normalized,
                            "name": mapping[normalized],
                        },
                    ),
                ),
            )
            return await self.world_store.save_state(updated)

    async def unlock_event(
        self,
        event_id: str,
        *,
        at: datetime | None = None,
        config: RefugeCasinoConfig | None = None,
    ) -> RefugeWorldState:
        return await self._unlock_marker(
            event_id,
            mapping=CASINO_EVENTS,
            state_key="casino_events",
            event_type="casino_event_discovered",
            at=at,
            config=config,
        )

    async def unlock_secret(
        self,
        secret_id: str,
        *,
        at: datetime | None = None,
        config: RefugeCasinoConfig | None = None,
    ) -> RefugeWorldState:
        return await self._unlock_marker(
            secret_id,
            mapping=CASINO_SECRET_EVENTS,
            state_key="secret_events",
            event_type="casino_secret_discovered",
            at=at,
            config=config,
        )


refuge_casino_service = RefugeCasinoService()


__all__ = [
    "CASINO_BUILDING_ID",
    "CASINO_EVENTS",
    "CASINO_FORTUNE_NAMES",
    "CASINO_LEVEL_NAMES",
    "CASINO_MAX_LEVEL",
    "CASINO_RECENT_WINDOW_SECONDS",
    "CASINO_SECRET_EVENTS",
    "CasinoSourceSnapshot",
    "RefugeCasinoConfig",
    "RefugeCasinoService",
    "RefugeCasinoStatus",
    "casino_fortune_for_net",
    "casino_is_open",
    "casino_level_name",
    "casino_prestige_points",
    "casino_source_snapshot",
    "refuge_casino_service",
]
