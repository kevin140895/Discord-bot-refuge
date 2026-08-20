from __future__ import annotations

import discord

from config import PARI_XP_CHANNEL_ID
from models.refuge_world import RefugeWorldState
from rendering.refuge_world import RefugeRenderContext
from services.casino_reactions import CasinoReactionState
from services.refuge_panel import RefugePanelSnapshot
from ui.refuge_panel_view import (
    REFUGE_MAP_FILENAME,
    RefugeCasinoPortalView,
    RefugePublicControlsView,
    RefugePublicPanelView,
)


def _snapshot(
    *,
    reaction: CasinoReactionState | None = None,
    public_legends: int = 0,
    secrets: int = 0,
) -> RefugePanelSnapshot:
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
        casino_reaction=reaction or CasinoReactionState(),
        casino_public_legend_count=public_legends,
        casino_public_legend_total=4,
        casino_secret_legend_count=secrets,
        casino_secret_legend_total=3,
        construction_label="Aucun chantier actif",
        latest_event_id=None,
        latest_event_label=None,
        visual_signature="visual",
        summary_signature="summary",
        changed=False,
    )


def _buttons(view):
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _panel_text(view) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_public_panel_is_persistent_v2_with_one_five_button_row():
    view = RefugePublicPanelView(_snapshot())
    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout is None
    buttons = _buttons(view)
    assert [button.label for button in buttons] == [
        "Explorer",
        "Mon empreinte",
        "Chronologie",
        "Chantier",
        "Casino",
    ]
    assert [button.custom_id for button in buttons] == [
        "refuge:panel:explore",
        "refuge:panel:footprint",
        "refuge:panel:timeline",
        "refuge:panel:construction",
        "refuge:panel:casino",
    ]
    rows = [item for item in view.walk_children() if isinstance(item, discord.ui.ActionRow)]
    assert len(rows) == 1
    assert len(rows[0].children) == 5


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


def test_refuge_011_keeps_legacy_actions_and_adds_only_casino_portal():
    controls = RefugePublicControlsView()
    assert {button.custom_id for button in _buttons(controls)} == {
        "refuge:panel:explore",
        "refuge:panel:footprint",
        "refuge:panel:timeline",
        "refuge:panel:construction",
        "refuge:panel:casino",
    }


def test_public_panel_surfaces_casino_reaction_and_only_secret_count():
    reaction = CasinoReactionState(
        activity="busy",
        reaction="green_zero",
        bets_10m=8,
        unique_players_10m=4,
    )
    text = _panel_text(
        RefugePublicPanelView(
            _snapshot(reaction=reaction, public_legends=2, secrets=1)
        )
    )
    assert "Éclat du Zéro Vert" in text
    assert "2/4 légendes" in text
    assert "1/3 mystères" in text
    assert "Chat Noir" not in text
    assert "Diamant" not in text
    assert "Joueur Fantôme" not in text


def test_casino_portal_links_to_existing_channel_without_new_discord_object():
    guild_id = 1400549418767220886
    view = RefugeCasinoPortalView(_snapshot(), guild_id=guild_id)
    buttons = _buttons(view)
    assert len(buttons) == 1
    assert buttons[0].style is discord.ButtonStyle.link
    assert buttons[0].custom_id is None
    assert buttons[0].url == (
        f"https://discord.com/channels/{guild_id}/{PARI_XP_CHANNEL_ID}"
    )
