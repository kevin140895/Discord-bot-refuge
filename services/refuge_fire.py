from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Final, Mapping

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
from storage.refuge_activity_store import (
    RefugeActivityStore,
    refuge_activity_store,
)


FIRE_BUILDING_ID: Final[str] = "fire"
FIRE_METRIC_KEY: Final[str] = "community_voice_seconds"
FIRE_MAX_LEVEL: Final[int] = 5
FIRE_RECENT_WINDOW_SECONDS: Final[int] = 24 * 60 * 60

FIRE_LEVEL_NAMES: Final[Mapping[int, str]] = {
    1: "L’Étincelle",
    2: "Le Campement",
    3: "Le Grand Foyer",
    4: "La Place du Refuge",
    5: "Le Cœur du Refuge",
}

FIRE_SECRET_EVENTS: Final[Mapping[str, str]] = {
    "night_of_stars": "La Nuit des Étoiles",
    "first_visitor": "Le Premier Visiteur",
    "full_circle": "Le Cercle complet",
}

_VALID_INTENSITIES = frozenset({"low", "normal", "high"})


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse_thresholds_env(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    values: list[int] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            raise ValueError(f"{name} contains an empty threshold")
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError(f"{name} must contain integer seconds") from exc
        values.append(value)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class RefugeFireConfig:
    """Configurable thresholds; no production thresholds are hard-coded."""

    level_thresholds_seconds: tuple[int, ...] = ()
    intensity_thresholds_seconds: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.level_thresholds_seconds and len(self.level_thresholds_seconds) != 4:
            raise ValueError(
                "fire level thresholds must contain exactly 4 values "
                "to define levels II to V"
            )
        if self.intensity_thresholds_seconds and len(
            self.intensity_thresholds_seconds
        ) != 2:
            raise ValueError(
                "fire intensity thresholds must contain exactly 2 values"
            )
        self._validate_increasing(
            self.level_thresholds_seconds,
            name="fire level thresholds",
            positive=True,
        )
        self._validate_increasing(
            self.intensity_thresholds_seconds,
            name="fire intensity thresholds",
            positive=False,
        )

    @staticmethod
    def _validate_increasing(
        values: tuple[int, ...],
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
    def from_env(cls) -> "RefugeFireConfig":
        return cls(
            level_thresholds_seconds=_parse_thresholds_env(
                "REFUGE_FIRE_LEVEL_THRESHOLDS_SECONDS"
            ),
            intensity_thresholds_seconds=_parse_thresholds_env(
                "REFUGE_FIRE_INTENSITY_THRESHOLDS_SECONDS"
            ),
        )


@dataclass(frozen=True, slots=True)
class RefugeFireStatus:
    state: RefugeWorldState
    level: int
    level_name: str
    intensity: str
    lifetime_voice_seconds: int
    recent_voice_seconds: int
    changed: bool
    render_signature: str


def fire_level_name(level: int) -> str:
    normalized = max(1, min(FIRE_MAX_LEVEL, int(level)))
    return FIRE_LEVEL_NAMES[normalized]


def fire_intensity_for_recent(
    recent_seconds: int,
    *,
    thresholds: tuple[int, ...] = (),
) -> str:
    recent = max(0, int(recent_seconds))
    if thresholds:
        if len(thresholds) != 2:
            raise ValueError("fire intensity thresholds must contain 2 values")
        low_to_normal, normal_to_high = (int(value) for value in thresholds)
        if recent < low_to_normal:
            return "low"
        if recent < normal_to_high:
            return "normal"
        return "high"

    # Without calibrated production thresholds, zero activity is quiet and
    # any observed community voice activity is shown as normal. "high" stays
    # unavailable until the two explicit thresholds are configured.
    return "low" if recent == 0 else "normal"


def _fire_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == FIRE_BUILDING_ID
        ),
        None,
    )


def _replace_building(
    state: RefugeWorldState,
    building: RefugeBuildingState,
) -> RefugeWorldState:
    buildings = {
        current.building_id: current
        for current in state.buildings
    }
    buildings[building.building_id] = building
    return replace(
        state,
        buildings=tuple(
            sorted(buildings.values(), key=lambda item: item.building_id)
        ),
    )


class RefugeFireService:
    """Bridge real community voice activity to the permanent Refuge Fire."""

    def __init__(
        self,
        *,
        activity_store: RefugeActivityStore = refuge_activity_store,
        world_service: RefugeWorldService = refuge_world_service,
    ) -> None:
        self.activity_store = activity_store
        self.world_service = world_service
        self.world_store = world_service.store
        self._lock = asyncio.Lock()

    async def evaluate(
        self,
        *,
        config: RefugeFireConfig | None = None,
        at: datetime | None = None,
    ) -> RefugeFireStatus:
        fire_config = config or RefugeFireConfig.from_env()
        async with self._lock:
            return await self._evaluate_locked(config=fire_config, at=at)

    async def _evaluate_locked(
        self,
        *,
        config: RefugeFireConfig,
        at: datetime | None,
    ) -> RefugeFireStatus:
        lifetime_seconds = await self.activity_store.get_total_seconds()
        recent_seconds = await self.activity_store.get_recent_seconds(
            window_seconds=FIRE_RECENT_WINDOW_SECONDS,
            at=at,
        )
        progression = await self.world_service.evaluate(
            metrics={FIRE_METRIC_KEY: lifetime_seconds},
            rules=(
                BuildingProgressionRule(
                    building_id=FIRE_BUILDING_ID,
                    metric_key=FIRE_METRIC_KEY,
                    thresholds=config.level_thresholds_seconds,
                    minimum_level=1,
                    event_from_level=2,
                ),
            ),
            at=at,
        )

        state = progression.state
        building = _fire_building(state)
        if building is None:
            raise RuntimeError("Refuge Fire progression did not create a building")

        intensity = fire_intensity_for_recent(
            recent_seconds,
            thresholds=config.intensity_thresholds_seconds,
        )
        if intensity not in _VALID_INTENSITIES:
            raise RuntimeError(f"unsupported Fire intensity: {intensity}")

        state_changed = progression.changed
        if building.state.get("intensity") != intensity:
            building_state = dict(building.state)
            building_state["intensity"] = intensity
            building = replace(building, state=building_state)
            state = _replace_building(state, building)
            state = await self.world_store.save_state(state)
            state_changed = True

        return RefugeFireStatus(
            state=state,
            level=max(1, min(FIRE_MAX_LEVEL, int(building.level))),
            level_name=fire_level_name(building.level),
            intensity=intensity,
            lifetime_voice_seconds=lifetime_seconds,
            recent_voice_seconds=recent_seconds,
            changed=state_changed,
            render_signature=world_render_signature(state),
        )

    async def unlock_secret(
        self,
        secret_id: str,
        *,
        at: datetime | None = None,
        config: RefugeFireConfig | None = None,
    ) -> RefugeWorldState:
        """Persist one supported Fire secret; trigger conditions live in REFUGE-012."""

        normalized = str(secret_id).strip()
        if normalized not in FIRE_SECRET_EVENTS:
            raise ValueError(f"unsupported Fire secret: {normalized}")

        fire_config = config or RefugeFireConfig.from_env()
        async with self._lock:
            status = await self._evaluate_locked(config=fire_config, at=at)
            state = status.state
            building = _fire_building(state)
            if building is None:
                raise RuntimeError("Refuge Fire building is missing")

            event_id = f"fire:secret:{normalized}"
            if any(event.event_id == event_id for event in state.events):
                return state

            existing_secrets = building.state.get("secret_events", ())
            secrets = {
                str(value)
                for value in existing_secrets
                if str(value) in FIRE_SECRET_EVENTS
            } if isinstance(existing_secrets, (list, tuple, set, frozenset)) else set()
            secrets.add(normalized)

            building_state = dict(building.state)
            building_state["secret_events"] = sorted(secrets)
            updated = _replace_building(
                state,
                replace(building, state=building_state),
            )

            event = RefugeHistoricalEvent(
                event_id=event_id,
                event_type="fire_secret_discovered",
                occurred_at=_utc_iso(at),
                data={
                    "building_id": FIRE_BUILDING_ID,
                    "secret_id": normalized,
                    "name": FIRE_SECRET_EVENTS[normalized],
                },
            )
            updated = replace(updated, events=updated.events + (event,))
            return await self.world_store.save_state(updated)


refuge_fire_service = RefugeFireService()


__all__ = [
    "FIRE_BUILDING_ID",
    "FIRE_LEVEL_NAMES",
    "FIRE_MAX_LEVEL",
    "FIRE_RECENT_WINDOW_SECONDS",
    "FIRE_SECRET_EVENTS",
    "RefugeFireConfig",
    "RefugeFireService",
    "RefugeFireStatus",
    "fire_intensity_for_recent",
    "fire_level_name",
    "refuge_fire_service",
]
