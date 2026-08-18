from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from cogs.refuge_panel import (
    build_refuge_live_status,
    refuge_ambience,
    refuge_day_number,
    refuge_member_count,
    refuge_radio_status,
    refuge_voice_count,
)
from models.refuge_world import RefugeWorldState
from rendering.refuge_world import RefugeRenderContext
from services.refuge_panel import RefugePanelSnapshot
from ui.refuge_panel_view import RefugeLiveStatus, RefugePublicPanelView


def _snapshot(*, created_at: str = "2026-08-18T00:00:00+00:00") -> RefugePanelSnapshot:
    return RefugePanelSnapshot(
        state=RefugeWorldState(created_at=created_at),
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


def _panel_text(view: RefugePublicPanelView) -> str:
    container = view.children[0]
    return "\n".join(
        item.content
        for item in container.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_refuge_day_number_uses_persisted_creation_date_and_paris_calendar() -> None:
    created_at = "2026-08-18T00:00:00+00:00"
    assert refuge_day_number(
        created_at,
        at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
    ) == 1
    assert refuge_day_number(
        created_at,
        at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
    ) == 3
    assert refuge_day_number("not-a-date") is None


@pytest.mark.parametrize(
    ("hour_utc", "expected"),
    [
        (4, "Le Refuge s’éveille doucement."),
        (10, "L’activité bat son plein dans le Refuge."),
        (17, "Les habitants se retrouvent autour du feu."),
        (22, "Le Refuge s’endort, mais quelques lumières restent allumées."),
    ],
)
def test_refuge_ambience_follows_paris_daypart(hour_utc: int, expected: str) -> None:
    # 18 August is CEST (UTC+2), so the selected UTC hours map to
    # Paris 06:00, 12:00, 19:00 and 00:00 respectively.
    assert refuge_ambience(
        at=datetime(2026, 8, 18, hour_utc, tzinfo=timezone.utc)
    ) == expected


def test_live_counts_exclude_bots() -> None:
    human_a = SimpleNamespace(bot=False)
    human_b = SimpleNamespace(bot=False)
    human_c = SimpleNamespace(bot=False)
    bot_member = SimpleNamespace(bot=True)
    guild = SimpleNamespace(
        member_count=4,
        members=[human_a, human_b, human_c, bot_member],
        voice_channels=[
            SimpleNamespace(members=[human_a, bot_member]),
            SimpleNamespace(members=[human_b]),
        ],
    )

    assert refuge_member_count(guild) == 3
    assert refuge_voice_count(guild) == 2


def test_radio_status_reuses_existing_radio_cog_state() -> None:
    class ConnectedVoice:
        def is_connected(self) -> bool:
            return True

    class DisconnectedVoice:
        def is_connected(self) -> bool:
            return False

    def bot_with(radio):
        return SimpleNamespace(get_cog=lambda name: radio if name == "RadioCog" else None)

    assert refuge_radio_status(bot_with(None)) == "Hors ligne"
    assert refuge_radio_status(
        bot_with(SimpleNamespace(stream_url=None, voice=ConnectedVoice()))
    ) == "En pause"
    assert refuge_radio_status(
        bot_with(SimpleNamespace(stream_url="https://radio", voice=ConnectedVoice()))
    ) == "En ligne"
    assert refuge_radio_status(
        bot_with(SimpleNamespace(stream_url="https://radio", voice=DisconnectedVoice()))
    ) == "Reconnexion"


def test_live_status_is_displayed_on_existing_public_panel() -> None:
    human = SimpleNamespace(bot=False)
    guild = SimpleNamespace(
        member_count=1,
        members=[human],
        voice_channels=[SimpleNamespace(members=[human])],
    )

    class ConnectedVoice:
        def is_connected(self) -> bool:
            return True

    radio = SimpleNamespace(stream_url="https://radio", voice=ConnectedVoice())
    bot = SimpleNamespace(
        get_cog=lambda name: radio if name == "RadioCog" else None
    )
    snapshot = _snapshot()
    status = build_refuge_live_status(
        bot,
        guild,
        snapshot,
        at=datetime(2026, 8, 18, 19, tzinfo=timezone.utc),
    )

    assert status == RefugeLiveStatus(
        day_number=1,
        member_count=1,
        voice_count=1,
        radio_status="En ligne",
        ambience="Les habitants se retrouvent autour du feu.",
    )

    text = _panel_text(RefugePublicPanelView(snapshot, live_status=status))
    assert "Vie du Refuge" in text
    assert "Jour 1" in text
    assert "1 habitants" in text
    assert "1 au feu de camp" in text
    assert "Radio : En ligne" in text
    assert "Les habitants se retrouvent autour du feu." in text


def test_live_signature_changes_when_runtime_state_changes() -> None:
    base = RefugeLiveStatus(
        day_number=1,
        member_count=10,
        voice_count=2,
        radio_status="En ligne",
        ambience="Les habitants se retrouvent autour du feu.",
    )
    changed = RefugeLiveStatus(
        day_number=1,
        member_count=10,
        voice_count=3,
        radio_status="En ligne",
        ambience="Les habitants se retrouvent autour du feu.",
    )

    assert base.signature != changed.signature
