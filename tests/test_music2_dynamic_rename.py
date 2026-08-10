from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.music2_dynamic_rename as dynamic_rename


class DummyVoice:
    def __init__(self, playing: bool = True, paused: bool = False) -> None:
        self._playing = playing
        self._paused = paused

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused


class DummyChannel:
    def __init__(self, name: str = "📻・Radio-HipHop") -> None:
        self.name = name
        self.id = dynamic_rename.RADIO_VC_ID


class DummyBot:
    def __init__(self, *, music=None, radio=None, channel=None) -> None:
        self.music = music
        self.radio = radio
        self.channel = channel

    def get_cog(self, name: str):
        if name == "Music2Cog":
            return self.music
        if name == "RadioCog":
            return self.radio
        return None

    def get_channel(self, channel_id: int):
        if channel_id == dynamic_rename.RADIO_VC_ID:
            return self.channel
        return None


@pytest.mark.parametrize(
    ("uploader", "expected"),
    [
        ("La Fouine - Topic", "La Fouine"),
        ("StromaeVEVO", "Stromae"),
        ("  Ninho   ", "Ninho"),
    ],
)
def test_clean_artist_removes_youtube_channel_suffixes(uploader, expected):
    assert dynamic_rename.Music2DynamicRenameCog._clean_artist(uploader) == expected


def test_artist_falls_back_to_title_prefix():
    track = SimpleNamespace(uploader=None, title="La Fouine - Du Ferme")

    artist = dynamic_rename.Music2DynamicRenameCog._artist_for_track(track)

    assert artist == "La Fouine"


def test_channel_name_uses_recording_dot_and_stays_within_discord_limit():
    track = SimpleNamespace(uploader="A" * 150, title="Ignored")

    name = dynamic_rename.Music2DynamicRenameCog._channel_name_for_track(track)

    assert name.startswith("🔴・")
    assert len(name) == 100


@pytest.mark.asyncio
async def test_active_custom_track_requests_dynamic_channel_name(monkeypatch):
    track = SimpleNamespace(uploader="La Fouine - Topic", title="Du Ferme")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyChannel()
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    request = AsyncMock()
    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)
    monkeypatch.setattr(dynamic_rename.rename_manager, "request", request)

    await cog._sync_current_track_name()

    request.assert_awaited_once_with(channel, "🔴・La Fouine")


@pytest.mark.asyncio
async def test_repeated_sync_does_not_queue_same_artist_while_cache_is_stale(monkeypatch):
    track = SimpleNamespace(uploader="Bad Bunny - Topic", title="MONACO")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyChannel("📻・Radio-HipHop")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    request = AsyncMock()
    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)
    monkeypatch.setattr(dynamic_rename.rename_manager, "request", request)

    await cog._sync_current_track_name()
    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    request.assert_awaited_once_with(channel, "🔴・Bad Bunny")


@pytest.mark.asyncio
async def test_radio_return_requests_station_name_after_custom_track(monkeypatch):
    track = SimpleNamespace(uploader="Bad Bunny - Topic", title="MONACO")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(
        stream_url=None,
        voice=DummyVoice(playing=True),
        _rename_for_stream=AsyncMock(),
    )
    channel = DummyChannel("📻・Radio-HipHop")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    request = AsyncMock()
    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)
    monkeypatch.setattr(dynamic_rename.rename_manager, "request", request)

    await cog._sync_current_track_name()
    request.assert_awaited_once_with(channel, "🔴・Bad Bunny")

    music.current = None
    radio.stream_url = "https://radio.example/live"
    channel.name = "🔴・Bad Bunny"

    await cog._sync_current_track_name()

    radio._rename_for_stream.assert_awaited_once_with(
        channel, "https://radio.example/live"
    )


@pytest.mark.asyncio
async def test_radio_station_sync_is_requested_once_per_stream(monkeypatch):
    music = SimpleNamespace(current=None)
    radio = SimpleNamespace(
        stream_url="https://radio.example/live",
        voice=DummyVoice(playing=True),
        _rename_for_stream=AsyncMock(),
    )
    channel = DummyChannel("🔴・Bad Bunny")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)

    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    radio._rename_for_stream.assert_awaited_once_with(
        channel, "https://radio.example/live"
    )
