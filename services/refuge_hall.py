from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Mapping, Sequence

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
from storage.achievement_store import AchievementStore, achievement_store
from utils.achievements import ACHIEVEMENT_BY_ID
from utils.seasons import season_id_for


HALL_BUILDING_ID: Final[str] = "hall"
HALL_METRIC_KEY: Final[str] = "hall_progression_points"
HALL_MAX_LEVEL: Final[int] = 5
HALL_RECENT_SHOWCASE_SECONDS: Final[int] = 24 * 60 * 60

HALL_LEVEL_NAMES: Final[Mapping[int, str]] = {
    1: "Cabane des Souvenirs",
    2: "Salle des Trophées",
    3: "Hall des Légendes",
    4: "Panthéon du Refuge",
    5: "Archives Éternelles",
}

HALL_SECRET_EVENTS: Final[Mapping[str, str]] = {
    "memory_flame": "Flamme du Souvenir",
    "endless_book": "Livre sans fin",
    "forgotten_crown": "Couronne oubliée",
}


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _utc_iso(at: datetime | None = None) -> str:
    return _aware_utc(at).isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_int_tuple_env(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    values: list[int] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            raise ValueError(f"{name} contains an empty value")
        try:
            values.append(int(token))
        except ValueError as exc:
            raise ValueError(f"{name} must contain integers") from exc
    return tuple(values)


def _parse_str_tuple_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    return tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))


def _env_non_negative_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True, slots=True)
class HallUnlockEvent:
    user_id: int
    achievement_id: str
    unlocked_at: datetime


@dataclass(frozen=True, slots=True)
class HallHistoricalFirst:
    achievement_id: str
    user_id: int
    unlocked_at: str


@dataclass(frozen=True, slots=True)
class HallRareShowcase:
    achievement_id: str
    user_id: int
    unlocked_at: str
    expires_at: str
    unlock_count: int
    achiever_count: int
    prevalence_per_10000: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "achievement_id": self.achievement_id,
            "user_id": self.user_id,
            "unlocked_at": self.unlocked_at,
            "expires_at": self.expires_at,
            "unlock_count": self.unlock_count,
            "achiever_count": self.achiever_count,
            "prevalence_per_10000": self.prevalence_per_10000,
        }


@dataclass(frozen=True, slots=True)
class HallSignalSnapshot:
    total_unlocks: int
    unique_achievers: int
    unique_achievement_ids: int
    category_diversity: int
    historical_first_count: int
    rarity_units: int
    achievement_counts: tuple[tuple[str, int], ...]
    historical_firsts: tuple[HallHistoricalFirst, ...]
    rare_showcase: HallRareShowcase | None


@dataclass(frozen=True, slots=True)
class RefugeHallConfig:
    """Configurable Hall progression. Defaults intentionally keep level I."""

    level_thresholds_points: tuple[int, ...] = ()
    unlock_weight: int = 0
    achiever_weight: int = 0
    diversity_weight: int = 0
    historical_first_weight: int = 0
    rarity_weight: int = 0
    historical_first_achievement_ids: tuple[str, ...] = ()
    gallery_unlock_milestones: tuple[int, ...] = ()
    gallery_achiever_milestones: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.level_thresholds_points and len(self.level_thresholds_points) != 4:
            raise ValueError(
                "hall level thresholds must contain exactly 4 values "
                "to define levels II to V"
            )
        self._validate_increasing(
            self.level_thresholds_points,
            name="hall level thresholds",
            positive=True,
        )
        self._validate_increasing(
            self.gallery_unlock_milestones,
            name="hall gallery unlock milestones",
            positive=True,
        )
        self._validate_increasing(
            self.gallery_achiever_milestones,
            name="hall gallery achiever milestones",
            positive=True,
        )
        for name, value in (
            ("unlock_weight", self.unlock_weight),
            ("achiever_weight", self.achiever_weight),
            ("diversity_weight", self.diversity_weight),
            ("historical_first_weight", self.historical_first_weight),
            ("rarity_weight", self.rarity_weight),
        ):
            if int(value) < 0:
                raise ValueError(f"{name} must be >= 0")
        unknown = [
            achievement_id
            for achievement_id in self.historical_first_achievement_ids
            if achievement_id not in ACHIEVEMENT_BY_ID
        ]
        if unknown:
            raise ValueError(
                "unknown historical-first achievement ids: " + ", ".join(unknown)
            )

    @staticmethod
    def _validate_increasing(
        values: Sequence[int],
        *,
        name: str,
        positive: bool,
    ) -> None:
        previous: int | None = None
        for raw in values:
            value = int(raw)
            if positive and value <= 0:
                raise ValueError(f"{name} must be > 0")
            if not positive and value < 0:
                raise ValueError(f"{name} must be >= 0")
            if previous is not None and value <= previous:
                raise ValueError(f"{name} must be strictly increasing")
            previous = value

    @classmethod
    def from_env(cls) -> "RefugeHallConfig":
        return cls(
            level_thresholds_points=_parse_int_tuple_env(
                "REFUGE_HALL_LEVEL_THRESHOLDS_POINTS"
            ),
            unlock_weight=_env_non_negative_int("REFUGE_HALL_UNLOCK_WEIGHT"),
            achiever_weight=_env_non_negative_int("REFUGE_HALL_ACHIEVER_WEIGHT"),
            diversity_weight=_env_non_negative_int("REFUGE_HALL_DIVERSITY_WEIGHT"),
            historical_first_weight=_env_non_negative_int(
                "REFUGE_HALL_HISTORICAL_FIRST_WEIGHT"
            ),
            rarity_weight=_env_non_negative_int("REFUGE_HALL_RARITY_WEIGHT"),
            historical_first_achievement_ids=_parse_str_tuple_env(
                "REFUGE_HALL_HISTORICAL_FIRST_IDS"
            ),
            gallery_unlock_milestones=_parse_int_tuple_env(
                "REFUGE_HALL_GALLERY_UNLOCK_MILESTONES"
            ),
            gallery_achiever_milestones=_parse_int_tuple_env(
                "REFUGE_HALL_GALLERY_ACHIEVER_MILESTONES"
            ),
        )

    def progression_points(self, signals: HallSignalSnapshot) -> int:
        return (
            signals.total_unlocks * int(self.unlock_weight)
            + signals.unique_achievers * int(self.achiever_weight)
            + signals.category_diversity * int(self.diversity_weight)
            + signals.historical_first_count * int(self.historical_first_weight)
            + signals.rarity_units * int(self.rarity_weight)
        )


@dataclass(frozen=True, slots=True)
class RefugeHallStatus:
    state: RefugeWorldState
    level: int
    level_name: str
    progression_points: int
    signals: HallSignalSnapshot
    changed: bool
    render_signature: str


def hall_level_name(level: int) -> str:
    normalized = max(1, min(HALL_MAX_LEVEL, int(level)))
    return HALL_LEVEL_NAMES[normalized]


def _hall_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == HALL_BUILDING_ID
        ),
        None,
    )


def _replace_building(
    state: RefugeWorldState,
    building: RefugeBuildingState,
) -> RefugeWorldState:
    buildings = {current.building_id: current for current in state.buildings}
    buildings[building.building_id] = building
    return replace(
        state,
        buildings=tuple(
            sorted(buildings.values(), key=lambda item: item.building_id)
        ),
    )


def _achievement_events(snapshot: Mapping[str, Any]) -> tuple[HallUnlockEvent, ...]:
    users = snapshot.get("users", {})
    if not isinstance(users, Mapping):
        return ()
    events: list[HallUnlockEvent] = []
    for raw_user_id, raw_achievements in users.items():
        if not isinstance(raw_achievements, Mapping):
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        for raw_achievement_id, raw_unlocked_at in raw_achievements.items():
            achievement_id = str(raw_achievement_id)
            unlocked_at = _parse_timestamp(raw_unlocked_at)
            if unlocked_at is None:
                continue
            events.append(
                HallUnlockEvent(
                    user_id=user_id,
                    achievement_id=achievement_id,
                    unlocked_at=unlocked_at,
                )
            )
    events.sort(
        key=lambda event: (
            event.unlocked_at,
            event.achievement_id,
            event.user_id,
        )
    )
    return tuple(events)


def _build_signals(
    events: Sequence[HallUnlockEvent],
    *,
    config: RefugeHallConfig,
    at: datetime,
) -> HallSignalSnapshot:
    counts: dict[str, int] = {}
    users: set[int] = set()
    categories: set[str] = set()
    by_achievement: dict[str, list[HallUnlockEvent]] = {}

    for event in events:
        users.add(event.user_id)
        counts[event.achievement_id] = counts.get(event.achievement_id, 0) + 1
        by_achievement.setdefault(event.achievement_id, []).append(event)
        definition = ACHIEVEMENT_BY_ID.get(event.achievement_id)
        if definition is not None:
            categories.add(definition.category)

    historical_firsts: list[HallHistoricalFirst] = []
    for achievement_id in config.historical_first_achievement_ids:
        candidates = by_achievement.get(achievement_id, ())
        if not candidates:
            continue
        first = min(
            candidates,
            key=lambda event: (event.unlocked_at, event.user_id),
        )
        historical_firsts.append(
            HallHistoricalFirst(
                achievement_id=achievement_id,
                user_id=first.user_id,
                unlocked_at=first.unlocked_at.isoformat(),
            )
        )

    achiever_count = len(users)
    rarity_units = sum(
        max(0, achiever_count - unlock_count)
        for unlock_count in counts.values()
    )

    window_start = at - timedelta(seconds=HALL_RECENT_SHOWCASE_SECONDS)
    recent = [
        event
        for event in events
        if window_start <= event.unlocked_at <= at
    ]
    showcase: HallRareShowcase | None = None
    if recent and achiever_count > 0:
        recent.sort(
            key=lambda event: (
                counts.get(event.achievement_id, 0),
                -event.unlocked_at.timestamp(),
                event.achievement_id,
                event.user_id,
            )
        )
        selected = recent[0]
        unlock_count = counts.get(selected.achievement_id, 0)
        prevalence = int(round((unlock_count / achiever_count) * 10000))
        showcase = HallRareShowcase(
            achievement_id=selected.achievement_id,
            user_id=selected.user_id,
            unlocked_at=selected.unlocked_at.isoformat(),
            expires_at=(
                selected.unlocked_at
                + timedelta(seconds=HALL_RECENT_SHOWCASE_SECONDS)
            ).isoformat(),
            unlock_count=unlock_count,
            achiever_count=achiever_count,
            prevalence_per_10000=prevalence,
        )

    return HallSignalSnapshot(
        total_unlocks=len(events),
        unique_achievers=achiever_count,
        unique_achievement_ids=len(counts),
        category_diversity=len(categories),
        historical_first_count=len(historical_firsts),
        rarity_units=rarity_units,
        achievement_counts=tuple(sorted(counts.items())),
        historical_firsts=tuple(historical_firsts),
        rare_showcase=showcase,
    )


def _season_plaques(
    events: Sequence[HallUnlockEvent],
    *,
    world_created_at: str | None,
) -> list[dict[str, Any]]:
    created = _parse_timestamp(world_created_at)
    by_season: dict[str, dict[str, Any]] = {}
    for event in events:
        if created is not None and event.unlocked_at < created:
            continue
        season_id = season_id_for(event.unlocked_at)
        payload = by_season.setdefault(
            season_id,
            {"season_id": season_id, "unlock_count": 0, "user_ids": set()},
        )
        payload["unlock_count"] += 1
        payload["user_ids"].add(event.user_id)

    plaques: list[dict[str, Any]] = []
    for season_id in sorted(by_season):
        payload = by_season[season_id]
        plaques.append(
            {
                "season_id": season_id,
                "unlock_count": int(payload["unlock_count"]),
                "unique_achievers": len(payload["user_ids"]),
            }
        )
    return plaques


def _gallery_markers(
    events: Sequence[HallUnlockEvent],
    *,
    world_created_at: str | None,
    unlock_milestones: Sequence[int],
    achiever_milestones: Sequence[int],
    existing: object,
) -> list[dict[str, Any]]:
    created = _parse_timestamp(world_created_at)
    eligible = [
        event
        for event in events
        if created is None or event.unlocked_at >= created
    ]
    markers: dict[str, dict[str, Any]] = {}
    if isinstance(existing, (list, tuple)):
        for item in existing:
            if not isinstance(item, Mapping):
                continue
            marker_id = str(item.get("marker_id", "")).strip()
            if marker_id:
                markers[marker_id] = dict(item)

    if eligible:
        first = eligible[0]
        markers.setdefault(
            "first_refuge_achievement",
            {
                "marker_id": "first_refuge_achievement",
                "kind": "first_achievement",
                "occurred_at": first.unlocked_at.isoformat(),
                "achievement_id": first.achievement_id,
                "user_id": first.user_id,
            },
        )

    for milestone in unlock_milestones:
        index = int(milestone) - 1
        if index < 0 or index >= len(eligible):
            continue
        event = eligible[index]
        markers.setdefault(
            f"achievement_unlock:{milestone}",
            {
                "marker_id": f"achievement_unlock:{milestone}",
                "kind": "achievement_unlock_milestone",
                "occurred_at": event.unlocked_at.isoformat(),
                "achievement_id": event.achievement_id,
                "user_id": event.user_id,
                "unlock_number": int(milestone),
            },
        )

    first_by_user: dict[int, HallUnlockEvent] = {}
    for event in eligible:
        first_by_user.setdefault(event.user_id, event)
    first_achievers = sorted(
        first_by_user.values(),
        key=lambda event: (event.unlocked_at, event.user_id),
    )
    for milestone in achiever_milestones:
        index = int(milestone) - 1
        if index < 0 or index >= len(first_achievers):
            continue
        event = first_achievers[index]
        markers.setdefault(
            f"unique_achiever:{milestone}",
            {
                "marker_id": f"unique_achiever:{milestone}",
                "kind": "unique_achiever_milestone",
                "occurred_at": event.unlocked_at.isoformat(),
                "achievement_id": event.achievement_id,
                "user_id": event.user_id,
                "achiever_number": int(milestone),
            },
        )

    return [markers[key] for key in sorted(markers)]


def _merge_historical_firsts(
    existing: object,
    discovered: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(existing, (list, tuple)):
        for item in existing:
            if not isinstance(item, Mapping):
                continue
            achievement_id = str(item.get("achievement_id", "")).strip()
            if achievement_id:
                merged[achievement_id] = dict(item)
    for item in discovered:
        achievement_id = str(item.get("achievement_id", "")).strip()
        if achievement_id:
            merged.setdefault(achievement_id, dict(item))
    return [merged[key] for key in sorted(merged)]


def _merge_season_plaques(
    existing: object,
    computed: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(existing, (list, tuple)):
        for item in existing:
            if not isinstance(item, Mapping):
                continue
            season_id = str(item.get("season_id", "")).strip()
            if season_id:
                merged[season_id] = dict(item)
    for item in computed:
        season_id = str(item.get("season_id", "")).strip()
        if not season_id:
            continue
        previous = merged.get(season_id, {})
        merged[season_id] = {
            "season_id": season_id,
            "unlock_count": max(
                int(previous.get("unlock_count", 0) or 0),
                int(item.get("unlock_count", 0) or 0),
            ),
            "unique_achievers": max(
                int(previous.get("unique_achievers", 0) or 0),
                int(item.get("unique_achievers", 0) or 0),
            ),
        }
    return [merged[key] for key in sorted(merged)]


class RefugeHallService:
    """Project persisted achievement history into the living Hall."""

    def __init__(
        self,
        *,
        achievement_store_: AchievementStore = achievement_store,
        world_service: RefugeWorldService = refuge_world_service,
    ) -> None:
        self.achievement_store = achievement_store_
        self.world_service = world_service
        self.world_store = world_service.store
        self._lock = asyncio.Lock()

    async def evaluate(
        self,
        *,
        config: RefugeHallConfig | None = None,
        at: datetime | None = None,
    ) -> RefugeHallStatus:
        hall_config = config or RefugeHallConfig.from_env()
        async with self._lock:
            return await self._evaluate_locked(config=hall_config, at=at)

    async def _evaluate_locked(
        self,
        *,
        config: RefugeHallConfig,
        at: datetime | None,
    ) -> RefugeHallStatus:
        now = _aware_utc(at)
        snapshot = await self.achievement_store.get_snapshot()
        events = _achievement_events(snapshot)
        signals = _build_signals(events, config=config, at=now)
        progression_points = config.progression_points(signals)

        progression = await self.world_service.evaluate(
            metrics={HALL_METRIC_KEY: progression_points},
            rules=(
                BuildingProgressionRule(
                    building_id=HALL_BUILDING_ID,
                    metric_key=HALL_METRIC_KEY,
                    thresholds=config.level_thresholds_points,
                    minimum_level=1,
                    event_from_level=2,
                ),
            ),
            at=now,
        )
        state = progression.state
        building = _hall_building(state)
        if building is None:
            raise RuntimeError("Refuge Hall progression did not create a building")

        building_state = dict(building.state)
        desired_showcase = (
            signals.rare_showcase.to_dict()
            if signals.rare_showcase is not None
            else None
        )
        discovered_firsts = [
            {
                "achievement_id": first.achievement_id,
                "user_id": first.user_id,
                "unlocked_at": first.unlocked_at,
            }
            for first in signals.historical_firsts
        ]
        desired_firsts = _merge_historical_firsts(
            building_state.get("historical_firsts"),
            discovered_firsts,
        )
        desired_plaques = _merge_season_plaques(
            building_state.get("season_plaques"),
            _season_plaques(
                events,
                world_created_at=state.created_at,
            ),
        )
        desired_gallery = _gallery_markers(
            events,
            world_created_at=state.created_at,
            unlock_milestones=config.gallery_unlock_milestones,
            achiever_milestones=config.gallery_achiever_milestones,
            existing=building_state.get("gallery_markers"),
        )

        changed_visual = False
        for key, desired in (
            ("rare_showcase", desired_showcase),
            ("historical_firsts", desired_firsts),
            ("season_plaques", desired_plaques),
            ("gallery_markers", desired_gallery),
        ):
            if building_state.get(key) != desired:
                if desired is None:
                    building_state.pop(key, None)
                else:
                    building_state[key] = desired
                changed_visual = True

        state_changed = progression.changed
        if changed_visual:
            building = replace(building, state=building_state)
            state = _replace_building(state, building)
            state = await self.world_store.save_state(state)
            state_changed = True

        return RefugeHallStatus(
            state=state,
            level=max(1, min(HALL_MAX_LEVEL, int(building.level))),
            level_name=hall_level_name(building.level),
            progression_points=progression_points,
            signals=signals,
            changed=state_changed,
            render_signature=world_render_signature(state),
        )

    async def record_gallery_marker(
        self,
        marker_id: str,
        *,
        kind: str,
        occurred_at: datetime | None = None,
        data: Mapping[str, Any] | None = None,
        config: RefugeHallConfig | None = None,
    ) -> RefugeWorldState:
        """Persist one Hall-owned community-history marker idempotently."""

        normalized = str(marker_id).strip()
        marker_kind = str(kind).strip()
        if not normalized:
            raise ValueError("marker_id is required")
        if not marker_kind:
            raise ValueError("kind is required")

        hall_config = config or RefugeHallConfig.from_env()
        async with self._lock:
            status = await self._evaluate_locked(config=hall_config, at=occurred_at)
            state = status.state
            building = _hall_building(state)
            if building is None:
                raise RuntimeError("Refuge Hall building is missing")

            building_state = dict(building.state)
            raw_markers = building_state.get("gallery_markers", ())
            markers = [
                dict(item)
                for item in raw_markers
                if isinstance(item, Mapping)
            ] if isinstance(raw_markers, (list, tuple)) else []
            if any(str(item.get("marker_id")) == normalized for item in markers):
                return state

            marker = {
                "marker_id": normalized,
                "kind": marker_kind,
                "occurred_at": _utc_iso(occurred_at),
            }
            if data:
                marker["data"] = dict(data)
            markers.append(marker)
            markers.sort(key=lambda item: str(item.get("marker_id", "")))
            building_state["gallery_markers"] = markers
            updated = _replace_building(
                state,
                replace(building, state=building_state),
            )
            event = RefugeHistoricalEvent(
                event_id=f"hall:gallery:{normalized}",
                event_type="hall_gallery_marker",
                occurred_at=_utc_iso(occurred_at),
                data={
                    "building_id": HALL_BUILDING_ID,
                    "marker_id": normalized,
                    "kind": marker_kind,
                },
            )
            if not any(item.event_id == event.event_id for item in updated.events):
                updated = replace(updated, events=updated.events + (event,))
            return await self.world_store.save_state(updated)

    async def unlock_secret(
        self,
        secret_id: str,
        *,
        at: datetime | None = None,
        config: RefugeHallConfig | None = None,
    ) -> RefugeWorldState:
        """Persist one Hall secret; trigger conditions remain in REFUGE-012."""

        normalized = str(secret_id).strip()
        if normalized not in HALL_SECRET_EVENTS:
            raise ValueError(f"unsupported Hall secret: {normalized}")

        hall_config = config or RefugeHallConfig.from_env()
        async with self._lock:
            status = await self._evaluate_locked(config=hall_config, at=at)
            state = status.state
            building = _hall_building(state)
            if building is None:
                raise RuntimeError("Refuge Hall building is missing")

            event_id = f"hall:secret:{normalized}"
            if any(event.event_id == event_id for event in state.events):
                return state

            raw_secrets = building.state.get("secret_events", ())
            secrets = {
                str(value)
                for value in raw_secrets
                if str(value) in HALL_SECRET_EVENTS
            } if isinstance(raw_secrets, (list, tuple, set, frozenset)) else set()
            secrets.add(normalized)
            building_state = dict(building.state)
            building_state["secret_events"] = sorted(secrets)
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
                        event_type="hall_secret_discovered",
                        occurred_at=_utc_iso(at),
                        data={
                            "building_id": HALL_BUILDING_ID,
                            "secret_id": normalized,
                            "name": HALL_SECRET_EVENTS[normalized],
                        },
                    ),
                ),
            )
            return await self.world_store.save_state(updated)


refuge_hall_service = RefugeHallService()


__all__ = [
    "HALL_BUILDING_ID",
    "HALL_LEVEL_NAMES",
    "HALL_MAX_LEVEL",
    "HALL_RECENT_SHOWCASE_SECONDS",
    "HALL_SECRET_EVENTS",
    "HallHistoricalFirst",
    "HallRareShowcase",
    "HallSignalSnapshot",
    "RefugeHallConfig",
    "RefugeHallService",
    "RefugeHallStatus",
    "hall_level_name",
    "refuge_hall_service",
]
