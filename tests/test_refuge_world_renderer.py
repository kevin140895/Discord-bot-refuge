from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from PIL import Image

from models.refuge_world import RefugeBuildingState, RefugePanelState, RefugeWorldState
from rendering.refuge_world import (
    REFUGE_CANVAS_SIZE,
    RefugeRenderContext,
    RefugeWorldRenderer,
    daypart_for_hour,
    scene_render_signature,
    season_for_month,
)


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        (1, "winter"),
        (3, "spring"),
        (6, "summer"),
        (9, "autumn"),
        (12, "winter"),
    ],
)
def test_season_for_month(month: int, expected: str):
    assert season_for_month(month) == expected


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "night"),
        (6, "morning"),
        (9, "morning"),
        (10, "day"),
        (17, "day"),
        (18, "sunset"),
        (21, "sunset"),
        (22, "night"),
    ],
)
def test_daypart_for_hour(hour: int, expected: str):
    assert daypart_for_hour(hour) == expected


def test_render_context_converts_to_paris_time():
    # 04:30 UTC is 06:30 in Paris on 9 August 2026 (CEST).
    context = RefugeRenderContext.from_datetime(
        datetime(2026, 8, 9, 4, 30, tzinfo=timezone.utc)
    )

    assert context == RefugeRenderContext(season="summer", daypart="morning")


def test_invalid_context_values_are_rejected():
    with pytest.raises(ValueError):
        RefugeRenderContext(season="monsoon", daypart="day")
    with pytest.raises(ValueError):
        RefugeRenderContext(season="summer", daypart="late")
    with pytest.raises(ValueError):
        season_for_month(13)
    with pytest.raises(ValueError):
        daypart_for_hour(24)


def test_renderer_returns_expected_png_dimensions():
    renderer = RefugeWorldRenderer()
    png = renderer.render_png(
        RefugeWorldState(),
        context=RefugeRenderContext(season="summer", daypart="day"),
    )

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == REFUGE_CANVAS_SIZE
        assert image.mode == "RGB"


def test_renderer_is_byte_deterministic_for_same_scene():
    renderer = RefugeWorldRenderer()
    state = RefugeWorldState()
    context = RefugeRenderContext(season="autumn", daypart="sunset")

    first = renderer.render_png(state, context=context)
    second = renderer.render_png(state, context=context)

    assert first == second


def test_season_and_daypart_change_the_rendered_scene():
    renderer = RefugeWorldRenderer()
    state = RefugeWorldState()

    summer_day = renderer.render_png(
        state,
        context=RefugeRenderContext(season="summer", daypart="day"),
    )
    winter_day = renderer.render_png(
        state,
        context=RefugeRenderContext(season="winter", daypart="day"),
    )
    summer_night = renderer.render_png(
        state,
        context=RefugeRenderContext(season="summer", daypart="night"),
    )

    assert summer_day != winter_day
    assert summer_day != summer_night


def test_scene_signature_ignores_panel_metadata_but_includes_ambience():
    state = RefugeWorldState()
    moved_panel = RefugeWorldState(panel=RefugePanelState(channel_id=12, message_id=34))
    summer_day = RefugeRenderContext(season="summer", daypart="day")
    summer_night = RefugeRenderContext(season="summer", daypart="night")

    assert scene_render_signature(state, summer_day) == scene_render_signature(
        moved_panel,
        summer_day,
    )
    assert scene_render_signature(state, summer_day) != scene_render_signature(
        state,
        summer_night,
    )


def test_scene_signature_changes_with_visual_world_state():
    context = RefugeRenderContext(season="summer", daypart="day")
    empty = RefugeWorldState()
    changed = RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="fire",
                level=2,
                state={"intensity": "high"},
            ),
        )
    )

    assert scene_render_signature(empty, context) != scene_render_signature(
        changed,
        context,
    )


@pytest.mark.asyncio
async def test_async_renderer_matches_sync_renderer():
    renderer = RefugeWorldRenderer()
    state = RefugeWorldState()
    context = RefugeRenderContext(season="spring", daypart="morning")

    expected = renderer.render_png(state, context=context)
    actual = await renderer.render_png_async(state, context=context)

    assert actual == expected
