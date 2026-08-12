import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.music2_dynamic_rename as dynamic_status
import cogs.radio as radio_mod
from cogs.radio import RadioCog
from config import RADIO_VC_ID, ROCK_RADIO_STREAM_URL


class DummyVoice:
    def __init__(self, *, playing: bool = True, paused: bool = False) -> None:
        self._playing = playing
        self._paused = paused

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        self._playing = False
        self._paused = False


class DummyVoiceChannel:
    def __init__(self, name: str = "📻・Radio") -> None:
        self.id = RADIO_VC_ID
        self.name = name
        self.edit = AsyncMock()


class DynamicBot:
    def __init__(self, *, music, radio, channel) -> None:
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
        if channel_id == RADIO_VC_ID:
            return self.channel
        return None


class FakeResponse:
    def __init__(self) -> None:
        self._done = False
        self.defer = AsyncMock(side_effect=self._mark_done)
        self.send_message = AsyncMock()

    def _mark_done(self, *args, **kwargs) -> None:
        self._done = True

    def is_done(self) -> bool:
        return self._done


def test_radio_modules_have_no_rename_manager_dependency() -> None:
    """The fixed radio channel must never re-enter the generic rename queue."""

    assert not hasattr(dynamic_status, "rename_manager")
    assert not hasattr(radio_mod, "rename_manager")


@pytest.mark.asyncio
async def test_custom_track_updates_voice_status_without_touching_channel_name(
    monkeypatch,
) -> None:
    track = SimpleNamespace(uploader="Booba - Topic", title="DKR")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyVoiceChannel()
    original_name = channel.name
    bot = DynamicBot(music=music, radio=radio, channel=channel)
    cog = dynamic_status.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_status.discord, "VoiceChannel", DummyVoiceChannel)

    await cog._sync_current_track_name()

    assert channel.name == original_name
    channel.edit.assert_awaited_once_with(
        status="🎵 DKR",
        reason="RefugeBot: affichage dynamique Radio",
    )
    assert "name" not in channel.edit.await_args.kwargs


@pytest.mark.asyncio
async def test_status_transitions_are_sent_once_and_channel_name_stays_fixed(
    monkeypatch,
) -> None:
    track = SimpleNamespace(uploader="Bad Bunny - Topic", title="MONACO")
    music = SimpleNamespace(current=track)
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    channel = DummyVoiceChannel()
    original_name = channel.name
    bot = DynamicBot(music=music, radio=radio, channel=channel)
    cog = dynamic_status.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_status.discord, "VoiceChannel", DummyVoiceChannel)

    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    assert channel.edit.await_count == 1

    music.current = SimpleNamespace(uploader="Booba - Topic", title="DKR")
    await cog._sync_current_track_name()

    radio.stream_url = ROCK_RADIO_STREAM_URL
    music.current = None
    await cog._sync_current_track_name()
    await cog._sync_current_track_name()

    assert channel.name == original_name
    assert channel.edit.await_count == 3
    assert channel.edit.await_args_list[0].kwargs["status"] == "🎵 MONACO"
    assert channel.edit.await_args_list[1].kwargs["status"] == "🎵 DKR"
    assert channel.edit.await_args_list[2].kwargs["status"] == "☢️ Radio Rock"
    assert all("name" not in call.kwargs for call in channel.edit.await_args_list)


@pytest.mark.asyncio
async def test_failed_voice_status_write_is_not_cached_and_is_retried(monkeypatch) -> None:
    class FakeForbidden(Exception):
        pass

    channel = DummyVoiceChannel()
    channel.edit = AsyncMock(side_effect=[FakeForbidden(), None])
    music = SimpleNamespace(current=None)
    radio = SimpleNamespace(stream_url=ROCK_RADIO_STREAM_URL, voice=DummyVoice())
    bot = DynamicBot(music=music, radio=radio, channel=channel)
    cog = dynamic_status.Music2DynamicRenameCog(bot)

    monkeypatch.setattr(dynamic_status.discord, "VoiceChannel", DummyVoiceChannel)
    monkeypatch.setattr(dynamic_status.discord, "Forbidden", FakeForbidden)

    await cog._sync_current_track_name()
    assert cog._last_requested_status is None

    await cog._sync_current_track_name()

    assert channel.edit.await_count == 2
    assert cog._last_requested_status == "☢️ Radio Rock"


@pytest.mark.asyncio
async def test_radio_station_command_never_edits_radio_channel(monkeypatch) -> None:
    channel = DummyVoiceChannel()
    original_name = channel.name
    bot = SimpleNamespace(
        loop=asyncio.get_running_loop(),
        get_channel=lambda channel_id: channel if channel_id == RADIO_VC_ID else None,
    )
    cog = RadioCog(bot)
    cog.voice = DummyVoice(playing=True)
    monkeypatch.setattr(cog, "_connect_and_play", AsyncMock())

    interaction = SimpleNamespace(
        response=FakeResponse(),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.radio_rock(interaction)

    assert cog.stream_url == ROCK_RADIO_STREAM_URL
    assert channel.name == original_name
    channel.edit.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_compatibility_rename_helper_is_a_strict_noop() -> None:
    channel = DummyVoiceChannel()
    bot = SimpleNamespace(loop=asyncio.get_running_loop())
    cog = RadioCog(bot)

    await cog._rename_for_stream(channel, ROCK_RADIO_STREAM_URL)

    channel.edit.assert_not_awaited()
