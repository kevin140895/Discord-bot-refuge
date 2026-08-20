from __future__ import annotations

import io

from PIL import Image

from rendering.refuge_casino_reactions import apply_refuge_casino_reaction_overlay
from rendering.refuge_world import RefugeRenderContext
from services.casino_reactions import CasinoReactionState


def _base_png() -> bytes:
    image = Image.new("RGB", (1280, 720), (52, 76, 58))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_calm_casino_keeps_refuge_map_byte_identical():
    payload = _base_png()
    result = apply_refuge_casino_reaction_overlay(
        payload,
        CasinoReactionState(),
        context=RefugeRenderContext(season="summer", daypart="night", local_hour=1),
    )
    assert result == payload


def test_busy_casino_changes_refuge_map_without_changing_dimensions():
    payload = _base_png()
    reaction = CasinoReactionState(
        activity="busy",
        bets_10m=8,
        unique_players_10m=4,
    )
    result = apply_refuge_casino_reaction_overlay(
        payload,
        reaction,
        context=RefugeRenderContext(season="summer", daypart="night", local_hour=1),
    )

    assert result != payload
    image = Image.open(io.BytesIO(result))
    assert image.size == (1280, 720)


def test_green_zero_refuge_overlay_is_deterministic():
    payload = _base_png()
    reaction = CasinoReactionState(
        activity="active",
        reaction="green_zero",
        bets_10m=4,
        unique_players_10m=2,
    )
    context = RefugeRenderContext(season="summer", daypart="night", local_hour=2)

    first = apply_refuge_casino_reaction_overlay(payload, reaction, context=context)
    second = apply_refuge_casino_reaction_overlay(payload, reaction, context=context)

    assert first == second
    assert first != payload


def test_reaction_cache_key_ignores_exact_activity_counts():
    first = CasinoReactionState(
        activity="busy",
        bets_10m=8,
        unique_players_10m=4,
    )
    second = CasinoReactionState(
        activity="busy",
        bets_10m=25,
        unique_players_10m=9,
    )

    assert first.cache_key == second.cache_key == "busy-none"
