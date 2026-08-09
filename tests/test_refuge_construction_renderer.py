from __future__ import annotations

import io

from PIL import Image

from models.refuge_world import (
    RefugeBuildingState,
    RefugeConstructionState,
    RefugeWorldState,
)
from rendering.refuge_construction import RefugeConstructionRenderer
from rendering.refuge_world import REFUGE_CANVAS_SIZE, RefugeRenderContext


class _BaseRenderer:
    def render_png(self, state, *, context=None):
        image = Image.new("RGB", REFUGE_CANVAS_SIZE, (70, 95, 72))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=9)
        return buffer.getvalue()


def _render(state):
    renderer = RefugeConstructionRenderer(base_renderer=_BaseRenderer())
    context = RefugeRenderContext(season="summer", daypart="day")
    return renderer.render_png(state, context=context)


def test_construction_renderer_is_deterministic_and_keeps_canvas_contract():
    state = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="goal:abc",
            status="voting",
            opened_at="2026-08-09T12:00:00+00:00",
            closes_at="2026-08-12T12:00:00+00:00",
            data={"projects": [], "votes": {}},
        )
    )

    first = _render(state)
    second = _render(state)

    assert first == second
    image = Image.open(io.BytesIO(first))
    assert image.size == REFUGE_CANVAS_SIZE
    assert image.mode == "RGB"


def test_vote_tie_and_building_stages_have_distinct_visual_outputs():
    voting = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="goal:abc",
            status="voting",
            data={},
        )
    )
    tie = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="goal:abc",
            status="tie_break",
            data={"tied_project_ids": ["a", "b"]},
        )
    )
    building_0 = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="goal:abc",
            status="building",
            project_id="memory_garden",
            data={"visual_stage": 0},
        )
    )
    building_2 = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="goal:abc",
            status="building",
            project_id="memory_garden",
            data={"visual_stage": 2},
        )
    )

    outputs = {_render(voting), _render(tie), _render(building_0), _render(building_2)}
    assert len(outputs) == 4


def test_each_permanent_monument_changes_the_world_visual():
    states = []
    for building_id, name in (
        ("monument:star_observatory", "Observatoire des Étoiles"),
        ("monument:memory_garden", "Jardin des Souvenirs"),
        ("monument:lantern_tower", "Tour des Lanternes"),
    ):
        states.append(
            RefugeWorldState(
                buildings=(
                    RefugeBuildingState(
                        building_id=building_id,
                        level=1,
                        unlocked_at="2026-08-19T12:00:00+00:00",
                        state={"project_name": name},
                    ),
                )
            )
        )

    assert len({_render(state) for state in states}) == 3
