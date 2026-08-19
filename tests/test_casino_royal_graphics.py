from __future__ import annotations

from rendering import casino_royal
from rendering.casino_royal import CasinoVisualState


def _visual(
    *,
    phase: str = "night",
    fortune: str = "stable",
    is_open: bool = True,
    level: int = 1,
) -> CasinoVisualState:
    return CasinoVisualState(
        phase=phase,  # type: ignore[arg-type]
        local_hour=23,
        season="summer",
        fortune=fortune,
        fortune_name=fortune,
        is_open=is_open,
        level=level,
        recent_house_net_xp=0,
        world_signature="lot31-test",
    )


def _count_central_palette(image) -> tuple[int, int]:
    pixels = image.load()
    assert pixels is not None
    burgundy = 0
    antique_gold = 0
    for y in range(150, 660):
        for x in range(200, 1080):
            r, g, b = pixels[x, y]
            if r >= 45 and r >= g * 1.5 and r >= b * 1.15 and g < 70:
                burgundy += 1
            if r >= 130 and g >= 90 and b <= 150 and r >= g and g >= b * 0.7:
                antique_gold += 1
    return burgundy, antique_gold


def test_lot31_bumps_renderer_version_to_invalidate_old_cabin_cache():
    assert casino_royal.CASINO_ROYAL_RENDERER_VERSION == 2


def test_level_one_is_already_a_large_royal_casino_not_a_refuge_cabin():
    image = casino_royal._render_scene(_visual(level=1))
    burgundy, antique_gold = _count_central_palette(image)

    assert image.size == casino_royal.CASINO_ROYAL_SIZE
    # A level-one Casino must already dominate the hero with the approved
    # burgundy/antique-gold architecture. Progression only enriches it.
    assert burgundy > 40_000
    assert antique_gold > 30_000


def test_open_closed_and_fortune_states_keep_distinct_visuals():
    stable_open = casino_royal._render_scene(_visual())
    stable_closed = casino_royal._render_scene(_visual(is_open=False))
    ruined_open = casino_royal._render_scene(_visual(fortune="ruined"))
    insolent_open = casino_royal._render_scene(_visual(fortune="insolent"))

    assert stable_open.tobytes() != stable_closed.tobytes()
    assert ruined_open.tobytes() != stable_open.tobytes()
    assert insolent_open.tobytes() != stable_open.tobytes()


def test_progression_enriches_same_casino_identity_instead_of_replacing_it():
    level_one = casino_royal._render_scene(_visual(level=1))
    level_five = casino_royal._render_scene(_visual(level=5))
    burgundy_one, gold_one = _count_central_palette(level_one)
    burgundy_five, gold_five = _count_central_palette(level_five)

    assert burgundy_one > 40_000
    assert burgundy_five > 40_000
    assert gold_five > gold_one
