from __future__ import annotations

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from services.refuge_exploration import (
    RefugeExplorerSnapshot,
    RefugeExplorerZoneSnapshot,
)
from services.refuge_exploration_runtime import RefugeExplorationRuntimeService


class _Exploration:
    async def get_explorer(self, *, at=None):
        return RefugeExplorerSnapshot(
            zones=(
                RefugeExplorerZoneSnapshot("fire", "Le Feu", "🔥", ("Feu",)),
                RefugeExplorerZoneSnapshot("hall", "Le Hall", "🏆", ("Hall",)),
                RefugeExplorerZoneSnapshot("casino", "Le Casino", "🎰", ("Casino",)),
                RefugeExplorerZoneSnapshot("construction", "Le Chantier", "🏗️", ("Chantier",)),
                RefugeExplorerZoneSnapshot(
                    "monuments",
                    "Les Monuments",
                    "🗿",
                    ("Aucun monument communautaire n’est encore inscrit dans le Refuge.",),
                ),
                RefugeExplorerZoneSnapshot("mysteries", "Les Mystères", "🌌", ("Mystères",)),
            )
        )

    async def get_footprint(self, user_id, *, at=None):
        return (user_id, at)


class _WorldStore:
    def __init__(self, state):
        self.state = state

    async def get_state(self):
        return self.state


async def test_runtime_replaces_static_monument_zone_with_persisted_buildings():
    state = RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="monument:memory_garden",
                level=1,
                unlocked_at="2026-08-19T12:00:00+00:00",
                state={
                    "project_name": "Jardin des Souvenirs",
                    "description": "Un jardin permanent.",
                },
            ),
        )
    )
    service = RefugeExplorationRuntimeService(
        exploration_service=_Exploration(),
        world_store=_WorldStore(state),
    )

    snapshot = await service.get_explorer()
    monuments = snapshot.get_zone("monuments")

    assert "Constructions permanentes : 1." in monuments.details
    assert any("Jardin des Souvenirs" in row for row in monuments.history)
    assert any("19/08/2026" in row for row in monuments.history)
    assert tuple(zone.zone_id for zone in snapshot.zones) == (
        "fire",
        "hall",
        "casino",
        "construction",
        "monuments",
        "mysteries",
    )


async def test_runtime_keeps_empty_monument_copy_until_real_construction_exists():
    service = RefugeExplorationRuntimeService(
        exploration_service=_Exploration(),
        world_store=_WorldStore(RefugeWorldState()),
    )

    snapshot = await service.get_explorer()
    monuments = snapshot.get_zone("monuments")

    assert monuments.history == ()
    assert "Aucun monument communautaire" in monuments.details[0]
