from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.music2 as music2


class DummyBot:
    def __init__(self, radio=None) -> None:
        self.radio = radio
        self.loop = None

    def get_cog(self, name: str):
        return self.radio if name == "RadioCog" else None

    def get_channel(self, _channel_id: int):
        return None


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


@pytest.mark.asyncio
async def test_music2_view_contains_only_custom_music_controls():
    cog = music2.Music2Cog(DummyBot())

    assert isinstance(cog.view, discord.ui.LayoutView)
    assert cog.view.is_persistent()

    custom_ids = {
        item.custom_id for item in _buttons(cog.view) if getattr(item, "custom_id", None)
    }

    assert custom_ids == {
        "music2_add",
        "music2_pause_resume",
        "music2_next",
        "music2_queue",
        "music2_now_playing",
    }
    assert custom_ids == music2.MUSIC2_CUSTOM_IDS
    assert not custom_ids.intersection(
        {"radio_rap_fr", "radio_rap", "radio_rock", "radio_hiphop"}
    )


def test_music2_v2_idle_layout_exposes_current_queue_and_controls():
    cog = music2.Music2Cog(DummyBot())
    text = _text(cog.view)

    assert "## 🎵 MUSIQUE PERSONNALISÉE" in text
    assert "### ▶️ Lecture personnalisée" in text
    assert "Aucun titre en cours" in text
    assert "### 📃 File d'attente" in text
    assert "Vide" in text
    assert "### 🎛️ Contrôles de lecture" in text
    assert "### 🔄 Retour automatique" in text


def test_music2_v2_refresh_reflects_current_track_and_queue():
    cog = music2.Music2Cog(DummyBot())
    cog.current = music2.MusicTrack(
        title="One More Time",
        webpage_url="https://example.test/current",
        requester_id=42,
        duration=320,
    )
    cog.queue.append(
        music2.MusicTrack(
            title="Around the World",
            webpage_url="https://example.test/next",
            requester_id=43,
        )
    )

    cog.view.refresh()
    text = _text(cog.view)

    assert "### ▶️ Lecture en cours" in text
    assert "One More Time" in text
    assert "5:20" in text
    assert "<@42>" in text
    assert "1 titre(s)" in text


def test_music2_message_detection_handles_nested_components_v2():
    cog = music2.Music2Cog(DummyBot())
    message = SimpleNamespace(components=cog.view.children)

    assert cog._is_music_panel(message)


@pytest.mark.asyncio
async def test_render_music_panel_migrates_legacy_payload_in_place():
    cog = music2.Music2Cog(DummyBot())
    message = SimpleNamespace(edit=AsyncMock())

    await cog._render_music_panel(message)

    message.edit.assert_awaited_once()
    kwargs = message.edit.await_args.kwargs
    assert kwargs["content"] is None
    assert kwargs["embeds"] == []
    assert kwargs["attachments"] == []
    assert kwargs["view"] is cog.view
    assert isinstance(kwargs["view"], discord.ui.LayoutView)


@pytest.mark.asyncio
async def test_idle_panel_is_dedicated_to_custom_music():
    radio = SimpleNamespace(stream_url=music2.ROCK_RADIO_STREAM_URL)
    cog = music2.Music2Cog(DummyBot(radio))

    embed = cog.build_panel_embed()

    assert embed.title == "🎵 Musique personnalisée"
    assert embed.fields[0].value == "Aucun titre en cours."
    assert embed.fields[1].value == "Vide"
    assert "panneau séparé" in embed.footer.text


@pytest.mark.asyncio
async def test_panel_reports_current_track_and_queue_size():
    cog = music2.Music2Cog(DummyBot())
    cog.current = music2.MusicTrack(
        title="One More Time",
        webpage_url="https://example.test/current",
        requester_id=42,
        duration=320,
    )
    cog.queue.append(
        music2.MusicTrack(
            title="Around the World",
            webpage_url="https://example.test/next",
            requester_id=43,
        )
    )

    embed = cog.build_panel_embed()

    assert "One More Time" in embed.fields[0].value
    assert "5:20" in embed.fields[0].value
    assert embed.fields[1].value == "1 titre(s)"


def test_track_from_info_keeps_stable_webpage_url_and_metadata():
    cog = music2.Music2Cog(DummyBot())

    track = cog._track_from_info(
        {
            "title": "Test Song",
            "webpage_url": "https://example.test/watch/123",
            "url": "https://cdn.example.test/temporary-stream",
            "duration": 185,
            "uploader": "Artist",
        },
        requester_id=99,
    )

    assert track.title == "Test Song"
    assert track.webpage_url == "https://example.test/watch/123"
    assert track.duration == 185
    assert track.uploader == "Artist"
    assert track.requester_id == 99


def test_search_query_is_resolved_with_ytsearch1(monkeypatch):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, target, download=False):
            captured["target"] = target
            captured["download"] = download
            return {
                "entries": [
                    {
                        "title": "Found",
                        "webpage_url": "https://example.test/found",
                        "url": "https://cdn.example.test/found",
                    }
                ]
            }

    monkeypatch.setattr(music2.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    cog = music2.Music2Cog(DummyBot())

    result = cog._extract_info_sync("daft punk one more time")

    assert captured["target"] == "ytsearch1:daft punk one more time"
    assert captured["download"] is False
    assert captured["options"]["format"] == "bestaudio/best"
    assert result["title"] == "Found"


@pytest.mark.asyncio
async def test_manual_radio_takeover_cancels_on_demand_queue():
    radio = SimpleNamespace(stream_url=music2.RADIO_RAP_STREAM_URL)
    cog = music2.Music2Cog(DummyBot(radio))
    cog.current = music2.MusicTrack(
        title="Current",
        webpage_url="https://example.test/current",
        requester_id=1,
    )
    cog.queue.append(
        music2.MusicTrack(
            title="Queued",
            webpage_url="https://example.test/queued",
            requester_id=2,
        )
    )
    cog._radio_restore_stream = music2.RADIO_STREAM_URL
    cog._generation = 7
    cog.refresh_panel = AsyncMock()

    await cog._handle_track_end(7, None)

    assert cog.current is None
    assert list(cog.queue) == []
    assert cog._radio_restore_stream is None
    cog.refresh_panel.assert_awaited_once()


@pytest.mark.asyncio
async def test_natural_track_end_advances_queue_when_radio_is_suspended():
    radio = SimpleNamespace(stream_url=None)
    cog = music2.Music2Cog(DummyBot(radio))
    cog.current = music2.MusicTrack(
        title="Current",
        webpage_url="https://example.test/current",
        requester_id=1,
    )
    cog._generation = 3
    cog._play_next = AsyncMock()

    await cog._handle_track_end(3, None)

    assert cog.current is None
    cog._play_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_audio_callback_is_ignored():
    radio = SimpleNamespace(stream_url=None)
    cog = music2.Music2Cog(DummyBot(radio))
    current = music2.MusicTrack(
        title="Current",
        webpage_url="https://example.test/current",
        requester_id=1,
    )
    cog.current = current
    cog._generation = 10
    cog._play_next = AsyncMock()

    await cog._handle_track_end(9, None)

    assert cog.current is current
    cog._play_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_radio_uses_station_active_before_music():
    radio = SimpleNamespace(
        stream_url=None,
        _connect_and_play=AsyncMock(),
        _rename_for_stream=AsyncMock(),
    )
    cog = music2.Music2Cog(DummyBot(radio))
    cog._radio_restore_stream = music2.ROCK_RADIO_STREAM_URL

    await cog._restore_radio()

    assert radio.stream_url == music2.ROCK_RADIO_STREAM_URL
    assert cog._radio_restore_stream is None
    radio._connect_and_play.assert_awaited_once()
