from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from PIL import Image

import cogs.pari_xp as pari_xp
from rendering.casino_royal import (
    CASINO_ROYAL_FILENAME,
    CASINO_ROYAL_SIZE,
    CasinoRoyalRenderer,
    build_casino_visual_state,
    casino_visual_phase_for_hour,
)
from services.casino_visual_cache import CasinoVisualCache
from services.refuge_casino import (
    DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP,
    RefugeCasinoConfig,
    RefugeCasinoService,
    casino_fortune_for_net,
)
from services.refuge_world import RefugeWorldService
from storage.refuge_casino_activity_store import RefugeCasinoActivityStore
from storage.refuge_world_store import RefugeWorldStore


def _casino_service(tmp_path):
    state_file = tmp_path / "pari_xp_state.json"
    state_file.write_text("{}", encoding="utf-8")
    activity = RefugeCasinoActivityStore(tmp_path / "casino_activity.json")
    world_store = RefugeWorldStore(tmp_path / "refuge_world.json")
    world_service = RefugeWorldService(world_store)
    service = RefugeCasinoService(
        activity_store=activity,
        world_service=world_service,
        state_file=state_file,
    )
    return activity, service


def _view_text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


@pytest.mark.parametrize(
    ("net", "expected"),
    [
        (-1500, "ruined"),
        (-1499, "difficulty"),
        (-251, "difficulty"),
        (-250, "stable"),
        (250, "stable"),
        (251, "prosperous"),
        (1500, "prosperous"),
        (1501, "insolent"),
    ],
)
def test_approved_fortune_boundaries_are_exact(net, expected):
    assert casino_fortune_for_net(
        net,
        transactions=1,
        thresholds=DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP,
    ) == expected


def test_runtime_config_uses_approved_fortune_thresholds_when_env_is_absent(
    monkeypatch,
):
    monkeypatch.delenv("REFUGE_CASINO_FORTUNE_THRESHOLDS_XP", raising=False)
    assert (
        RefugeCasinoConfig.from_env().fortune_thresholds_xp
        == DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP
    )


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (4, "late_night"),
        (5, "dawn"),
        (8, "dawn"),
        (9, "day"),
        (16, "day"),
        (17, "golden"),
        (18, "golden"),
        (19, "dusk"),
        (21, "dusk"),
        (22, "night"),
        (0, "night"),
        (1, "night"),
        (2, "late_night"),
    ],
)
def test_visual_phase_boundaries(hour, expected):
    assert casino_visual_phase_for_hour(hour) == expected


@pytest.mark.parametrize("hour", [-1, 24])
def test_visual_phase_rejects_invalid_hours(hour):
    with pytest.raises(ValueError):
        casino_visual_phase_for_hour(hour)


@pytest.mark.asyncio
async def test_renderer_produces_16_by_9_png_and_distinct_visual_states(tmp_path):
    _activity, service = _casino_service(tmp_path)
    at = datetime(2026, 8, 19, 21, 30, tzinfo=timezone.utc)
    status = await service.evaluate(
        config=RefugeCasinoConfig(
            fortune_thresholds_xp=DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP
        ),
        at=at,
    )
    stable_night = build_casino_visual_state(
        status,
        at=at,
        phase_override="night",
        fortune_override="stable",
        open_override=True,
    )
    ruined_dawn = build_casino_visual_state(
        status,
        at=at,
        phase_override="dawn",
        fortune_override="ruined",
        open_override=False,
    )

    renderer = CasinoRoyalRenderer()
    first = renderer.render_png(status, stable_night)
    second = renderer.render_png(status, ruined_dawn)

    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert second.startswith(b"\x89PNG\r\n\x1a\n")
    assert first != second
    assert stable_night.cache_key != ruined_dawn.cache_key
    with Image.open(io.BytesIO(first)) as image:
        assert image.size == CASINO_ROYAL_SIZE
        assert image.mode == "RGB"


@pytest.mark.asyncio
async def test_visual_cache_reuses_same_render(tmp_path):
    _activity, service = _casino_service(tmp_path)
    at = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    status = await service.evaluate(
        config=RefugeCasinoConfig(
            fortune_thresholds_xp=DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP
        ),
        at=at,
    )
    cache = CasinoVisualCache(tmp_path / "casino_visuals")

    first = await cache.get_or_render(
        status,
        at=at,
        phase_override="day",
        fortune_override="stable",
        open_override=True,
    )
    second = await cache.get_or_render(
        status,
        at=at,
        phase_override="day",
        fortune_override="stable",
        open_override=True,
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.path == second.path
    assert first.signature == second.signature
    assert first.path.is_file()
    assert first.path.stat().st_size > 0


@pytest.mark.asyncio
async def test_panel_uses_attachment_gallery_and_never_shows_probabilities(tmp_path):
    _activity, service = _casino_service(tmp_path)
    at = datetime(2026, 8, 19, 21, 30, tzinfo=timezone.utc)
    status = await service.evaluate(
        config=RefugeCasinoConfig(
            fortune_thresholds_xp=DEFAULT_CASINO_FORTUNE_THRESHOLDS_XP
        ),
        at=at,
    )
    cache = CasinoVisualCache(tmp_path / "casino_visuals")
    asset = await cache.get_or_render(
        status,
        at=at,
        phase_override="night",
        fortune_override="prosperous",
        open_override=True,
    )
    cog = SimpleNamespace(
        is_open=True,
        casino_visual_asset=asset,
        living_state=pari_xp._empty_living_state(),
        state={
            "total_bets": 1234,
            "total_winnings": 567,
            "last_winner": {"user_id": 42, "amount": 20},
        },
    )

    view = pari_xp.RouletteXPView(cog, include_visual=True)
    galleries = [
        item for item in view.walk_children() if isinstance(item, discord.ui.MediaGallery)
    ]
    text = _view_text(view)

    assert len(galleries) == 1
    assert galleries[0].items[0].media.url == f"attachment://{CASINO_ROYAL_FILENAME}"
    assert "### 🏛️ Fortune de la Maison" in text
    assert "**Prospère** · reflet des dernières 24 h de jeu." in text
    assert "%" not in text
    assert "probabilit" not in text.lower()
    assert "🔴 Rouge / ⚫ Noir — **gain x2**" in text
    assert "🎯 Numéro (1-36) — **gain x10**" in text


def test_persistent_callback_view_does_not_reference_missing_attachment():
    cog = SimpleNamespace(
        is_open=True,
        casino_visual_asset=None,
        living_state=pari_xp._empty_living_state(),
        state={"total_bets": 0, "total_winnings": 0},
    )
    view = pari_xp.RouletteXPView(cog, include_visual=False)

    assert not any(
        isinstance(item, discord.ui.MediaGallery) for item in view.walk_children()
    )
    buttons = [
        item for item in view.walk_children() if isinstance(item, discord.ui.Button)
    ]
    assert {button.custom_id for button in buttons} == {
        "pari_xp:red",
        "pari_xp:black",
        "pari_xp:even",
        "pari_xp:odd",
        "pari_xp:number",
    }
