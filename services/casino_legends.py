from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from models.refuge_world import RefugeHistoricalEvent, RefugeWorldState
from services.refuge_casino import (
    CASINO_BUILDING_ID,
    CASINO_EVENTS,
    CASINO_SECRET_EVENTS,
    RefugeCasinoService,
    RefugeCasinoStatus,
    refuge_casino_service,
)
from storage.roulette_legend_store import (
    HOUSE_LEGEND_MIN_STREAK,
    RouletteLegendEvidence,
    RouletteLegendStore,
    roulette_legend_store,
)


CASINO_LEGEND_RULES_VERSION = 2
CASINO_LEGEND_RULES_PATCH = "2.1"
CASINO_LEGEND_V21_EVENT_ID = "casino:legend_rules:v2.1"
CASINO_LEGEND_DESCRIPTIONS = {
    "grand_heist": "Les joueurs ont fait plier les coffres de la Maison.",
    "black_night": "Une nuit entière a tourné à l'avantage de la Maison.",
    "break_in": "La serrure des numéros a cédé plusieurs fois au même joueur.",
    "house_always_wins": "Une série noire est désormais gravée dans les murs.",
}
CASINO_SECRET_DESCRIPTIONS = {
    "black_cat": "Le vert s'est répété jusqu'à faire apparaître un mauvais présage.",
    "diamond": "La mise parfaite a laissé un diamant dans les archives.",
    "ghost_player": "Un joueur solitaire a frappé pendant que le Refuge dormait.",
}


@dataclass(frozen=True, slots=True)
class CasinoLegendState:
    public_events: tuple[str, ...] = ()
    secret_events: tuple[str, ...] = ()

    @property
    def cache_key(self) -> str:
        payload = "|".join((*self.public_events, "#", *self.secret_events))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def is_notable(self) -> bool:
        return bool(self.public_events or self.secret_events)

    @property
    def public_names(self) -> tuple[str, ...]:
        return tuple(CASINO_EVENTS[event_id] for event_id in self.public_events)

    @property
    def secret_names(self) -> tuple[str, ...]:
        return tuple(CASINO_SECRET_EVENTS[event_id] for event_id in self.secret_events)


EMPTY_CASINO_LEGENDS = CasinoLegendState()


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _casino_building(status: RefugeCasinoStatus):
    return next(
        (
            item
            for item in status.state.buildings
            if item.building_id == CASINO_BUILDING_ID
        ),
        None,
    )


def _marker_sets(status: RefugeCasinoStatus) -> tuple[set[str], set[str]]:
    building = _casino_building(status)
    if building is None:
        return set(), set()

    raw_public = building.state.get("casino_events", ())
    raw_secret = building.state.get("secret_events", ())
    public: set[str] = set()
    secret: set[str] = set()
    if isinstance(raw_public, (list, tuple, set, frozenset)):
        public = {str(item) for item in raw_public if str(item) in CASINO_EVENTS}
    if isinstance(raw_secret, (list, tuple, set, frozenset)):
        secret = {
            str(item) for item in raw_secret if str(item) in CASINO_SECRET_EVENTS
        }
    return public, secret


def _legend_rules_meta(
    status: RefugeCasinoStatus,
) -> tuple[int, str | None, str | None, int | None]:
    building = _casino_building(status)
    if building is None:
        return 0, None, None, None
    try:
        version = int(building.state.get("legend_rules_version", 0))
    except (TypeError, ValueError):
        version = 0
    raw_started_at = building.state.get("legend_rules_v2_started_at")
    started_at = str(raw_started_at).strip() if raw_started_at else None
    raw_patch = building.state.get("legend_rules_patch")
    patch = str(raw_patch).strip() if raw_patch else None
    raw_boundary = building.state.get("legend_rules_v2_after_event_id")
    if raw_boundary is None:
        boundary = None
    else:
        try:
            boundary = max(0, int(raw_boundary))
        except (TypeError, ValueError):
            boundary = None
    return version, started_at, patch, boundary


def casino_legend_state_from_status(
    status: RefugeCasinoStatus,
    *,
    marker_override: str | None = None,
) -> CasinoLegendState:
    public, secret = _marker_sets(status)
    if marker_override:
        normalized = str(marker_override).strip()
        if normalized in CASINO_EVENTS:
            public.add(normalized)
        elif normalized in CASINO_SECRET_EVENTS:
            secret.add(normalized)
        else:
            raise ValueError(f"unsupported Casino legend marker: {normalized}")
    return CasinoLegendState(
        public_events=tuple(sorted(public)),
        secret_events=tuple(sorted(secret)),
    )


def _public_candidates(evidence: RouletteLegendEvidence) -> set[str]:
    candidates: set[str] = set()
    if evidence.grand_heist_qualified:
        candidates.add("grand_heist")
    if evidence.black_night_qualified:
        candidates.add("black_night")
    if evidence.break_in_qualified:
        candidates.add("break_in")
    if evidence.max_house_streak >= HOUSE_LEGEND_MIN_STREAK:
        candidates.add("house_always_wins")
    return candidates


def _secret_candidates(evidence: RouletteLegendEvidence) -> set[str]:
    candidates: set[str] = set()
    if evidence.black_cat_qualified:
        candidates.add("black_cat")
    if evidence.diamond_qualified:
        candidates.add("diamond")
    if evidence.ghost_player_qualified:
        candidates.add("ghost_player")
    return candidates


def _replace_casino_building(
    state: RefugeWorldState,
    *,
    building_state: dict[str, object],
) -> RefugeWorldState:
    buildings = []
    found = False
    for building in state.buildings:
        if building.building_id == CASINO_BUILDING_ID:
            buildings.append(replace(building, state=dict(building_state)))
            found = True
        else:
            buildings.append(building)
    if not found:
        return state
    return replace(
        state,
        buildings=tuple(sorted(buildings, key=lambda item: item.building_id)),
    )


class CasinoLegendService:
    """Unlock one-time narrative markers from real roulette history."""

    def __init__(
        self,
        *,
        store: RouletteLegendStore = roulette_legend_store,
        casino_service: RefugeCasinoService = refuge_casino_service,
    ) -> None:
        self.store = store
        self.casino_service = casino_service
        self._lock = asyncio.Lock()

    async def _migrate_v21(
        self,
        *,
        after_event_id: int,
        at: datetime | None,
    ) -> None:
        started_at = _utc_iso(at)
        removed_event_ids = {
            f"casino:casino_events:{marker_id}" for marker_id in CASINO_EVENTS
        }

        def updater(state: RefugeWorldState) -> RefugeWorldState:
            building = next(
                (
                    item
                    for item in state.buildings
                    if item.building_id == CASINO_BUILDING_ID
                ),
                None,
            )
            if building is None:
                return state

            building_state: dict[str, object] = dict(building.state)
            raw_patch = building_state.get("legend_rules_patch")
            patch = str(raw_patch).strip() if raw_patch else None
            raw_boundary = building_state.get("legend_rules_v2_after_event_id")
            try:
                existing_boundary = int(raw_boundary) if raw_boundary is not None else None
            except (TypeError, ValueError):
                existing_boundary = None
            if patch == CASINO_LEGEND_RULES_PATCH and existing_boundary is not None:
                return state

            raw_public = building_state.get("casino_events", ())
            if isinstance(raw_public, (list, tuple, set, frozenset)):
                reset_markers = sorted(
                    {str(item) for item in raw_public if str(item) in CASINO_EVENTS}
                )
            else:
                reset_markers = []

            building_state["casino_events"] = []
            building_state["legend_rules_version"] = CASINO_LEGEND_RULES_VERSION
            building_state["legend_rules_patch"] = CASINO_LEGEND_RULES_PATCH
            building_state["legend_rules_v2_after_event_id"] = max(
                0, int(after_event_id)
            )
            building_state["legend_rules_v21_started_at"] = started_at

            events = tuple(
                event for event in state.events if event.event_id not in removed_event_ids
            )
            if not any(event.event_id == CASINO_LEGEND_V21_EVENT_ID for event in events):
                events = events + (
                    RefugeHistoricalEvent(
                        event_id=CASINO_LEGEND_V21_EVENT_ID,
                        event_type="casino_legend_rules_migrated",
                        occurred_at=started_at,
                        data={
                            "building_id": CASINO_BUILDING_ID,
                            "rule_version": CASINO_LEGEND_RULES_VERSION,
                            "patch": CASINO_LEGEND_RULES_PATCH,
                            "after_event_id": max(0, int(after_event_id)),
                            "reset_markers": reset_markers,
                            "name": "Règles des légendes du Casino sécurisées V2.1",
                        },
                    ),
                )

            updated = _replace_casino_building(
                replace(state, events=events),
                building_state=building_state,
            )
            return updated

        # RefugeCasinoService owns all normal Casino world mutations. Sharing its
        # service lock here prevents a stale evaluate/save cycle from overwriting
        # the one-time V2.1 migration while RefugeWorldStore performs the atomic write.
        async with self.casino_service._lock:
            await self.casino_service.world_store.update_state(updater)

    async def sync(
        self,
        *,
        status: RefugeCasinoStatus | None = None,
        at: datetime | None = None,
    ) -> RefugeCasinoStatus:
        async with self._lock:
            current = status or await self.casino_service.evaluate(at=at)
            version, _started_at, patch, after_event_id = _legend_rules_meta(current)
            if version < CASINO_LEGEND_RULES_VERSION:
                await self.casino_service.migrate_legend_rules_v2(at=at)
                current = await self.casino_service.evaluate(at=at)
                version, _started_at, patch, after_event_id = _legend_rules_meta(current)

            if patch != CASINO_LEGEND_RULES_PATCH or after_event_id is None:
                boundary = await self.store.get_max_event_id()
                await self._migrate_v21(after_event_id=boundary, at=at)
                current = await self.casino_service.evaluate(at=at)
                version, _started_at, patch, after_event_id = _legend_rules_meta(current)

            if version < CASINO_LEGEND_RULES_VERSION:
                return current
            if patch != CASINO_LEGEND_RULES_PATCH or after_event_id is None:
                return current

            public, secret = _marker_sets(current)
            if public >= set(CASINO_EVENTS) and secret >= set(CASINO_SECRET_EVENTS):
                return current

            evidence = await self.store.get_evidence(
                at=at,
                after_event_id=after_event_id,
            )
            public_candidates = _public_candidates(evidence) - public
            secret_candidates = _secret_candidates(evidence) - secret
            changed = False

            for event_id in sorted(public_candidates):
                await self.casino_service.unlock_event(event_id, at=at)
                changed = True
            for secret_id in sorted(secret_candidates):
                await self.casino_service.unlock_secret(secret_id, at=at)
                changed = True

            if not changed:
                return current
            return await self.casino_service.evaluate(at=at)


casino_legend_service = CasinoLegendService()


__all__ = [
    "CASINO_LEGEND_DESCRIPTIONS",
    "CASINO_LEGEND_RULES_PATCH",
    "CASINO_LEGEND_RULES_VERSION",
    "CASINO_LEGEND_V21_EVENT_ID",
    "CASINO_SECRET_DESCRIPTIONS",
    "CasinoLegendService",
    "CasinoLegendState",
    "EMPTY_CASINO_LEGENDS",
    "casino_legend_service",
    "casino_legend_state_from_status",
]
