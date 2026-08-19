from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime

from services.refuge_casino import (
    CASINO_BUILDING_ID,
    CASINO_EVENTS,
    CASINO_SECRET_EVENTS,
    RefugeCasinoService,
    RefugeCasinoStatus,
    refuge_casino_service,
)
from storage.roulette_legend_store import (
    RouletteLegendEvidence,
    RouletteLegendStore,
    roulette_legend_store,
)


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


def _marker_sets(status: RefugeCasinoStatus) -> tuple[set[str], set[str]]:
    building = next(
        (
            item
            for item in status.state.buildings
            if item.building_id == CASINO_BUILDING_ID
        ),
        None,
    )
    if building is None:
        return set(), set()
    raw_public = building.state.get("casino_events", ())
    raw_secret = building.state.get("secret_events", ())
    public = {
        str(item)
        for item in raw_public
        if str(item) in CASINO_EVENTS
    } if isinstance(raw_public, (list, tuple, set, frozenset)) else set()
    secret = {
        str(item)
        for item in raw_secret
        if str(item) in CASINO_SECRET_EVENTS
    } if isinstance(raw_secret, (list, tuple, set, frozenset)) else set()
    return public, secret


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
    if evidence.max_house_streak >= 10:
        candidates.add("house_always_wins")
    return candidates


def _secret_candidates(evidence: RouletteLegendEvidence) -> set[str]:
    candidates: set[str] = set()
    if evidence.zero_count >= 3:
        candidates.add("black_cat")
    if evidence.max_payout_xp >= 5000:
        candidates.add("diamond")
    if evidence.ghost_player_qualified:
        candidates.add("ghost_player")
    return candidates


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

    async def sync(self, *, at: datetime | None = None) -> RefugeCasinoStatus:
        async with self._lock:
            status = await self.casino_service.evaluate(at=at)
            public, secret = _marker_sets(status)
            if public >= set(CASINO_EVENTS) and secret >= set(CASINO_SECRET_EVENTS):
                return status

            evidence = await self.store.get_evidence(at=at)
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
                return status
            return await self.casino_service.evaluate(at=at)


casino_legend_service = CasinoLegendService()


__all__ = [
    "CASINO_LEGEND_DESCRIPTIONS",
    "CASINO_SECRET_DESCRIPTIONS",
    "CasinoLegendService",
    "CasinoLegendState",
    "EMPTY_CASINO_LEGENDS",
    "casino_legend_service",
    "casino_legend_state_from_status",
]
