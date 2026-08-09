from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_casino import RefugeCasinoRenderer, casino_scene_signature
from rendering.refuge_world import REFUGE_CANVAS_SIZE, RefugeRenderContext


CONTEXT = RefugeRenderContext(season="summer", daypart="night")


def _state(
    *,
    level: int,
    fortune: str = "stable",
    is_open: bool = True,
    jackpot_tier: int | None = None,
    events: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> RefugeWorldState:
    state = {
        "fortune": fortune,
        "is_open": is_open,
        "casino_events": list(events),
        "secret_events": list(secrets),
    }
    if jackpot_tier is not None:
        state["last_jackpot"] = {
            "tier": jackpot_tier,
            "occurred_at": "2026-08-09T12:00:00+00:00",
        }
    return RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="casino",
                level=level,
                unlocked_at="2026-08-09T12:00:00+00:00",
                state=state,
            ),
        ),
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_casino_renderer_produces_expected_png_dimensions():
    payload = RefugeCasinoRenderer().render_png(_state(level=1), context=CONTEXT)
    image = Image.open(io.BytesIO(payload))
    assert image.size == REFUGE_CANVAS_SIZE
    assert image.mode == "RGB"


def test_all_five_casino_levels_have_distinct_silhouettes():
    renderer = RefugeCasinoRenderer()
    digests = {
        _digest(renderer.render_png(_state(level=level), context=CONTEXT))
        for level in range(1, 6)
    }
    assert len(digests) == 5


def test_all_five_fortune_states_change_the_visual():
    renderer = RefugeCasinoRenderer()
    digests = {
        _digest(
            renderer.render_png(
                _state(level=5, fortune=fortune),
                context=CONTEXT,
            )
        )
        for fortune in (
            "ruined",
            "difficulty",
            "stable",
            "prosperous",
            "insolent",
        )
    }
    assert len(digests) == 5


def test_open_and_closed_casino_have_distinct_lighting():
    renderer = RefugeCasinoRenderer()
    opened = renderer.render_png(
        _state(level=3, is_open=True),
        context=CONTEXT,
    )
    closed = renderer.render_png(
        _state(level=3, is_open=False),
        context=CONTEXT,
    )
    assert opened != closed


@pytest.mark.parametrize("tier", [500, 1000])
def test_jackpot_tiers_leave_visual_trace(tier):
    renderer = RefugeCasinoRenderer()
    base = renderer.render_png(_state(level=3), context=CONTEXT)
    jackpot = renderer.render_png(
        _state(level=3, jackpot_tier=tier),
        context=CONTEXT,
    )
    assert jackpot != base


@pytest.mark.parametrize(
    "event_id",
    ["grand_heist", "black_night", "break_in", "house_always_wins"],
)
def test_each_casino_event_adds_a_visual_trace(event_id):
    renderer = RefugeCasinoRenderer()
    base = renderer.render_png(_state(level=4), context=CONTEXT)
    discovered = renderer.render_png(
        _state(level=4, events=(event_id,)),
        context=CONTEXT,
    )
    assert discovered != base


@pytest.mark.parametrize(
    "secret_id",
    ["black_cat", "diamond", "ghost_player"],
)
def test_each_casino_secret_adds_a_visual_trace(secret_id):
    renderer = RefugeCasinoRenderer()
    base = renderer.render_png(_state(level=4), context=CONTEXT)
    discovered = renderer.render_png(
        _state(level=4, secrets=(secret_id,)),
        context=CONTEXT,
    )
    assert discovered != base


def test_casino_renderer_is_byte_deterministic():
    renderer = RefugeCasinoRenderer()
    state = _state(
        level=5,
        fortune="insolent",
        is_open=True,
        jackpot_tier=1000,
        events=("grand_heist", "house_always_wins"),
        secrets=("black_cat", "diamond", "ghost_player"),
    )
    first = renderer.render_png(state, context=CONTEXT)
    second = renderer.render_png(state, context=CONTEXT)
    assert first == second


def test_casino_scene_signature_tracks_visual_state():
    base = casino_scene_signature(_state(level=2), CONTEXT)
    level = casino_scene_signature(_state(level=3), CONTEXT)
    fortune = casino_scene_signature(
        _state(level=2, fortune="prosperous"),
        CONTEXT,
    )
    closed = casino_scene_signature(
        _state(level=2, is_open=False),
        CONTEXT,
    )
    jackpot = casino_scene_signature(
        _state(level=2, jackpot_tier=1000),
        CONTEXT,
    )
    assert len({base, level, fortune, closed, jackpot}) == 5


@pytest.mark.asyncio
async def test_casino_async_renderer_matches_sync_renderer():
    renderer = RefugeCasinoRenderer()
    state = _state(level=3, fortune="prosperous")
    expected = renderer.render_png(state, context=CONTEXT)
    actual = await renderer.render_png_async(state, context=CONTEXT)
    assert actual == expected
