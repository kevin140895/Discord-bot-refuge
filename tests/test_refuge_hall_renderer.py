from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_hall import RefugeHallRenderer, hall_scene_signature
from rendering.refuge_world import REFUGE_CANVAS_SIZE, RefugeRenderContext


CONTEXT = RefugeRenderContext(season="summer", daypart="night")


def _state(
    *,
    hall_level: int,
    showcase: bool = False,
    secrets: tuple[str, ...] = (),
) -> RefugeWorldState:
    hall_state = {
        "historical_firsts": [
            {
                "achievement_id": "level_5",
                "user_id": 1,
                "unlocked_at": "2026-08-09T05:00:00+00:00",
            }
        ],
        "season_plaques": [
            {
                "season_id": "2026-08",
                "unlock_count": 3,
                "unique_achievers": 2,
            }
        ],
        "gallery_markers": [
            {
                "marker_id": "first_refuge_achievement",
                "kind": "first_achievement",
                "occurred_at": "2026-08-09T05:00:00+00:00",
            }
        ],
        "secret_events": list(secrets),
    }
    if showcase:
        hall_state["rare_showcase"] = {
            "achievement_id": "casino_1_bet",
            "user_id": 2,
            "unlocked_at": "2026-08-09T05:10:00+00:00",
            "expires_at": "2026-08-10T05:10:00+00:00",
            "unlock_count": 1,
            "achiever_count": 4,
            "prevalence_per_10000": 2500,
        }

    return RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="fire",
                level=2,
                unlocked_at="2026-08-09T05:00:00+00:00",
                state={"intensity": "normal", "secret_events": []},
            ),
            RefugeBuildingState(
                building_id="hall",
                level=hall_level,
                unlocked_at="2026-08-09T05:00:00+00:00",
                state=hall_state,
            ),
        ),
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_hall_renderer_produces_expected_png_dimensions():
    payload = RefugeHallRenderer().render_png(_state(hall_level=1), context=CONTEXT)

    image = Image.open(io.BytesIO(payload))
    assert image.size == REFUGE_CANVAS_SIZE
    assert image.mode == "RGB"


def test_all_five_hall_levels_have_distinct_silhouettes():
    renderer = RefugeHallRenderer()

    digests = {
        _digest(renderer.render_png(_state(hall_level=level), context=CONTEXT))
        for level in range(1, 6)
    }

    assert len(digests) == 5


def test_hall_renderer_keeps_the_fire_layer_in_composition():
    renderer = RefugeHallRenderer()
    state = _state(hall_level=3)

    with_hall = renderer.render_png(state, context=CONTEXT)

    fire_only = RefugeWorldState(buildings=(state.buildings[0],))
    without_hall = renderer.render_png(fire_only, context=CONTEXT)

    assert with_hall != without_hall


def test_recent_rare_showcase_changes_the_visual_without_changing_level():
    renderer = RefugeHallRenderer()

    normal = renderer.render_png(_state(hall_level=3), context=CONTEXT)
    showcased = renderer.render_png(
        _state(hall_level=3, showcase=True),
        context=CONTEXT,
    )

    assert showcased != normal


@pytest.mark.parametrize(
    "secret_id",
    ["memory_flame", "endless_book", "forgotten_crown"],
)
def test_each_hall_secret_adds_a_permanent_visual_trace(secret_id):
    renderer = RefugeHallRenderer()

    base = renderer.render_png(_state(hall_level=4), context=CONTEXT)
    discovered = renderer.render_png(
        _state(hall_level=4, secrets=(secret_id,)),
        context=CONTEXT,
    )

    assert discovered != base


def test_hall_renderer_is_byte_deterministic():
    renderer = RefugeHallRenderer()
    state = _state(
        hall_level=5,
        showcase=True,
        secrets=("memory_flame", "endless_book", "forgotten_crown"),
    )

    first = renderer.render_png(state, context=CONTEXT)
    second = renderer.render_png(state, context=CONTEXT)

    assert first == second


def test_hall_scene_signature_tracks_hall_visual_state():
    base = hall_scene_signature(_state(hall_level=2), CONTEXT)
    level = hall_scene_signature(_state(hall_level=3), CONTEXT)
    showcase = hall_scene_signature(
        _state(hall_level=2, showcase=True),
        CONTEXT,
    )
    secret = hall_scene_signature(
        _state(hall_level=2, secrets=("memory_flame",)),
        CONTEXT,
    )

    assert len({base, level, showcase, secret}) == 4


@pytest.mark.asyncio
async def test_hall_async_renderer_matches_sync_renderer():
    renderer = RefugeHallRenderer()
    state = _state(hall_level=3, showcase=True)

    expected = renderer.render_png(state, context=CONTEXT)
    actual = await renderer.render_png_async(state, context=CONTEXT)

    assert actual == expected
