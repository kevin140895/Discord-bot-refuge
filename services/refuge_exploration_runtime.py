from __future__ import annotations

from datetime import datetime, timezone

from services.refuge_exploration import (
    RefugeExplorerSnapshot,
    RefugeExplorerZoneSnapshot,
    RefugeExplorationService,
    RefugeFootprintSnapshot,
    refuge_exploration_service,
)
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from utils.timezones import PARIS_TZ


def _format_date(value: object) -> str:
    if not value:
        return "date inconnue"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "date inconnue"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(PARIS_TZ).strftime("%d/%m/%Y")


def _monuments_zone(state) -> RefugeExplorerZoneSnapshot:
    monuments = [
        building
        for building in state.buildings
        if building.building_id.startswith("monument:") and int(building.level) > 0
    ]
    monuments.sort(key=lambda item: (item.unlocked_at or "", item.building_id))

    if not monuments:
        return RefugeExplorerZoneSnapshot(
            zone_id="monuments",
            title="Les Monuments",
            emoji="🗿",
            details=(
                "Aucun monument communautaire n’est encore inscrit dans le Refuge.",
                "Cette section accueille uniquement des constructions permanentes réellement obtenues par la communauté.",
            ),
        )

    history: list[str] = []
    for building in reversed(monuments):
        name = str(
            building.state.get("project_name") or building.building_id
        ).strip()
        description = str(building.state.get("description") or "").strip()
        row = f"{_format_date(building.unlocked_at)} · {name}"
        if description:
            row += f" — {description}"
        history.append(row)

    return RefugeExplorerZoneSnapshot(
        zone_id="monuments",
        title="Les Monuments",
        emoji="🗿",
        details=(
            f"Constructions permanentes : {len(monuments)}.",
            "Chaque monument provient d’un chantier réellement remporté par la communauté.",
        ),
        history=tuple(history),
    )


class RefugeExplorationRuntimeService:
    """Enrich REFUGE-009 exploration with construction-owned monument history."""

    def __init__(
        self,
        *,
        exploration_service: RefugeExplorationService = refuge_exploration_service,
        world_store: RefugeWorldStore = refuge_world_store,
    ) -> None:
        self.exploration_service = exploration_service
        self.world_store = world_store

    async def get_explorer(self, *, at=None) -> RefugeExplorerSnapshot:
        snapshot = await self.exploration_service.get_explorer(at=at)
        state = await self.world_store.get_state()
        replacement = _monuments_zone(state)
        zones = tuple(
            replacement if zone.zone_id == "monuments" else zone
            for zone in snapshot.zones
        )
        return RefugeExplorerSnapshot(zones=zones)

    async def get_footprint(self, user_id: int, *, at=None) -> RefugeFootprintSnapshot:
        return await self.exploration_service.get_footprint(user_id, at=at)


refuge_exploration_runtime_service = RefugeExplorationRuntimeService()


__all__ = [
    "RefugeExplorationRuntimeService",
    "refuge_exploration_runtime_service",
]
