from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_fire import (
    RefugeFireRenderer,
    fire_scene_signature,
)
from rendering.refuge_world import REFUGE_CANVAS_SIZE, RefugeRenderContext


CONTEXT = RefugeRenderContext(season="summer", daypart="night")


def _state(
    *,
    level: int,
    intensity: str = "normal",
    secrets: tuple[str, ...] = (),
) -> RefugeWorldState:
    return RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="fire",
                level=level,
                unlocked_at="2026-08-09T05:00:00+00:00",
                state={
                    "intensity": intensity,
                    "secret_events": list(secrets),
                },
            ),
        ),
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_fire_renderer_produces_expected_png_dimensions():
    payload = RefugeFireRenderer().render_png(
        _state(level=1),
        context=CONTEXT,
    )

    image = Image.open(io.BytesIO(payload))
    assert image.size == REFUGE_CANVAS_SIZE
    assert image.mode == "RGB"


def test_all_five_fire_levels_have_distinct_silhouettes():
    renderer = RefugeFireRenderer()

    digests = {
        _digest(renderer.render_png(_state(level=level), context=CONTEXT))
        for level in range(1, 6)
    }

    assert len(digests) == 5


def test_fire_intensity_changes_the_visual_without_changing_level():
    renderer = RefugeFireRenderer()
    low = renderer.render_png(
        _state(level=3, intensity="low"),
        context=CONTEXT,
    )
    normal = renderer.render_png(
        _state(level=3, intensity="normal"),
        context=CONTEXT,
    )
    high = renderer.render_png(
        _state(level=3, intensity="high"),
        context=CONTEXT,
    )

    assert len({_digest(low), _digest(normal), _digest(high)}) == 3


def test_fire_season_and_daypart_change_the_visual():
    renderer = RefugeFireRenderer()
    state = _state(level=4, intensity="high")

    summer_day = renderer.render_png(
        state,
        context=RefugeRenderContext(season="summer", daypart="day"),
    )
    winter_night = renderer.render_png(
        state,
        context=RefugeRenderContext(season="winter", daypart="night"),
    )

    assert summer_day != winter_night


@pytest.mark.parametrize(
    "secret_id",
    ["night_of_stars", "first_visitor", "full_circle"],
)
def test_each_fire_secret_adds_a_permanent_visual_trace(secret_id):
    renderer = RefugeFireRenderer()
    base = renderer.render_png(
        _state(level=3),
        context=CONTEXT,
    )
    discovered = renderer.render_png(
        _state(level=3, secrets=(secret_id,)),
        context=CONTEXT,
    )

    assert discovered != base


def test_fire_renderer_is_byte_deterministic():
    renderer = RefugeFireRenderer()
    state = _state(
        level=5,
        intensity="high",
        secrets=("night_of_stars", "first_visitor", "full_circle"),
    )

    first = renderer.render_png(state, context=CONTEXT)
    second = renderer.render_png(state, context=CONTEXT)

    assert first == second


def test_fire_scene_signature_tracks_level_intensity_and_secrets():
    base = fire_scene_signature(
        _state(level=2, intensity="normal"),
        CONTEXT,
    )
    level = fire_scene_signature(
        _state(level=3, intensity="normal"),
        CONTEXT,
    )
    intensity = fire_scene_signature(
        _state(level=2, intensity="high"),
        CONTEXT,
    )
    secret = fire_scene_signature(
        _state(level=2, intensity="normal", secrets=("full_circle",)),
        CONTEXT,
    )

    assert len({base, level, intensity, secret}) == 4


@pytest.mark.asyncio
async def test_fire_async_renderer_matches_sync_renderer():
    renderer = RefugeFireRenderer()
    state = _state(level=3, intensity="normal")

    expected = renderer.render_png(state, context=CONTEXT)
    actual = await renderer.render_png_async(state, context=CONTEXT)

    assert actual == expected
