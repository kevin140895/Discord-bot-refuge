from __future__ import annotations

import discord
import pytest

from models.refuge_world import RefugeWorldState
from rendering.refuge_world import RefugeRenderContext
from services.refuge_panel import RefugePanelSnapshot
from ui.refuge_panel_view import (
    REFUGE_MAP_FILENAME,
    RefugePendingActionView,
    RefugePublicControlsView,
    RefugePublicPanelView,
)


def _snapshot() -> RefugePanelSnapshot:
    return RefugePanelSnapshot(
        state=RefugeWorldState(),
        context=RefugeRenderContext(season="summer", daypart="day"),
        season_id="2026-08",
        season_label="Août 2026",
        fire_level=1,
        fire_name="L’Étincelle",
        fire_intensity="normal",
        fire_intensity_name="Vivant",
        hall_level=1,
        hall_name="Cabane des Souvenirs",
        casino_level=1,
        casino_name="Baraque de Jeux",
        casino_fortune="stable",
        casino_fortune_name="Stable",
        casino_is_open=True,
        construction_label="Aucun chantier actif",
        latest_event_id=None,
        latest_event_label=None,
        visual_signature="visual",
        summary_signature="summary",
        changed=False,
    )


def _buttons(view):
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def test_public_panel_is_persistent_v2_with_one_four_button_row():
    view = RefugePublicPanelView(_snapshot())
    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout is None
    buttons = _buttons(view)
    assert [button.label for button in buttons] == [
        "Explorer",
        "Mon empreinte",
        "Chronologie",
        "Chantier",
    ]
    assert [button.custom_id for button in buttons] == [
        "refuge:panel:explore",
        "refuge:panel:footprint",
        "refuge:panel:timeline",
        "refuge:panel:construction",
    ]
    rows = [item for item in view.walk_children() if isinstance(item, discord.ui.ActionRow)]
    assert len(rows) == 1
    assert len(rows[0].children) == 4


def test_public_panel_uses_attachment_media_gallery():
    view = RefugePublicPanelView(_snapshot())
    galleries = [
        item for item in view.walk_children() if isinstance(item, discord.ui.MediaGallery)
    ]
    assert len(galleries) == 1
    assert len(galleries[0].items) == 1
    assert galleries[0].items[0].media.url == f"attachment://{REFUGE_MAP_FILENAME}"


def test_callback_registration_view_is_persistent_and_has_same_custom_ids():
    public = RefugePublicPanelView(_snapshot())
    controls = RefugePublicControlsView()
    assert controls.timeout is None
    assert controls.is_persistent()
    assert [button.custom_id for button in _buttons(public)] == [
        button.custom_id for button in _buttons(controls)
    ]


def test_only_timeline_and_construction_remain_pending_after_refuge_009():
    for action, expected in (
        ("timeline", "Chronologie"),
        ("construction", "Chantier"),
    ):
        view = RefugePendingActionView(action)
        assert isinstance(view, discord.ui.LayoutView)
        assert view.timeout == 120
        text = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        assert expected in text

    with pytest.raises(ValueError):
        RefugePendingActionView("explore")
    with pytest.raises(ValueError):
        RefugePendingActionView("footprint")
