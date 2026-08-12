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
    def __init__(self, name: str = "📻・Radio") -> None:
        self.name = name
        self.id = dynamic_rename.RADIO_VC_ID
        self.edit = AsyncMock()


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


def test_voice_status_uses_track_title_and_stays_within_discord_limit():
    track = SimpleNamespace(uploader="Artist", title="T" * 600)

    status = dynamic_rename.Music2DynamicRenameCog._voice_status_for_track(track)

    assert status.startswith("🎵 ")
    assert len(status) == 500


@pytest.mark.parametrize(
    ("stream_url", "expected"),
    [
        (dynamic_rename.RADIO_STREAM_URL, "📻 Radio Hip-Hop"),
        (dynamic_rename.RADIO_RAP_STREAM_URL, "🔘 Radio Rap US"),
        (dynamic_rename.RADIO_RAP_FR_STREAM_URL, "🟣 Radio Rap FR"),
        (dynamic_rename.ROCK_RADIO_STREAM_URL, "☢️ Radio Rock"),
        ("https://radio.example/live", "📻 Radio"),
    ],
)
def test_voice_status_for_stream(stream_url, expected):
    assert (
        dynamic_rename.Music2DynamicRenameCog._voice_status_for_stream(stream_url)
        == expected
    )


def test_dynamic_radio_module_no_longer_depends_on_rename_manager():
    assert not hasattr(dynamic_rename, "rename_manager")


@pytest.mark.asyncio
async def test_active_custom_track_updates_status_without_changing_channel_name(monkeypatch):
    track = SimpleNamespace(uploader="La Fouine - Topic", title="Du Ferme")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyChannel("📻・Radio")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)

    await cog._sync_current_track_name()

    channel.edit.assert_awaited_once_with(
        status="🎵 Du Ferme",
        reason="RefugeBot: affichage dynamique Radio",
    )
    assert channel.name == "📻・Radio"
    assert "name" not in channel.edit.await_args.kwargs


@pytest.mark.asyncio
async def test_repeated_sync_does_not_repeat_same_status(monkeypatch):
    track = SimpleNamespace(uploader="Bad Bunny - Topic", title="MONACO")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyChannel("📻・Radio")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)

    await cog._sync_current_track_name()
    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    channel.edit.assert_awaited_once_with(
        status="🎵 MONACO",
        reason="RefugeBot: affichage dynamique Radio",
    )
    assert channel.name == "📻・Radio"


@pytest.mark.asyncio
async def test_radio_return_updates_station_status_without_rename(monkeypatch):
    track = SimpleNamespace(uploader="Bad Bunny - Topic", title="MONACO")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(
        stream_url=None,
        voice=DummyVoice(playing=True),
    )
    channel = DummyChannel("📻・Radio")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)

    await cog._sync_current_track_name()
    channel.edit.assert_awaited_once_with(
        status="🎵 MONACO",
        reason="RefugeBot: affichage dynamique Radio",
    )

    music.current = None
    radio.stream_url = "https://radio.example/live"
    channel.edit.reset_mock()

    await cog._sync_current_track_name()

    channel.edit.assert_awaited_once_with(
        status="📻 Radio",
        reason="RefugeBot: affichage dynamique Radio",
    )
    assert channel.name == "📻・Radio"


@pytest.mark.asyncio
async def test_radio_station_status_is_requested_once_per_stream(monkeypatch):
    music = SimpleNamespace(current=None)
    radio = SimpleNamespace(
        stream_url="https://radio.example/live",
        voice=DummyVoice(playing=True),
    )
    channel = DummyChannel("📻・Radio")
    bot = DummyBot(music=music, radio=radio, channel=channel)
    cog = dynamic_rename.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_rename.discord, "VoiceChannel", DummyChannel)

    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    channel.edit.assert_awaited_once_with(
        status="📻 Radio",
        reason="RefugeBot: affichage dynamique Radio",
    )
    assert channel.name == "📻・Radio"
