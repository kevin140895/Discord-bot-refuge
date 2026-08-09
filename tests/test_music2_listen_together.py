from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.music2_listen_together as listen_together


class DummyVoice:
    def __init__(self, *, playing: bool = True, paused: bool = False) -> None:
        self.playing = playing
        self.paused = paused

    def is_playing(self) -> bool:
        return self.playing

    def is_paused(self) -> bool:
        return self.paused


class DummyMember:
    def __init__(
        self,
        member_id: int,
        *,
        channel=None,
        bot: bool = False,
    ) -> None:
        self.id = member_id
        self.bot = bot
        self.voice = SimpleNamespace(channel=channel) if channel is not None else None
        self.move_to = AsyncMock()


class DummyVoiceChannel:
    def __init__(self, channel_id: int, members=None) -> None:
        self.id = channel_id
        self.members = list(members or [])


class DummyMessage:
    def __init__(self) -> None:
        self.edit = AsyncMock()
        self.delete = AsyncMock()
        self.embeds = []
        self.components = []
        self.author = SimpleNamespace(id=999)


class DummyTextChannel:
    def __init__(self, message: DummyMessage | None = None) -> None:
        self.message = message or DummyMessage()
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return self.message

    def history(self, *, limit: int):
        async def iterator():
            if False:
                yield None

        return iterator()


class DummyGuild:
    def __init__(self, voice_channel: DummyVoiceChannel) -> None:
        self.voice_channel = voice_channel

    def get_channel(self, channel_id: int):
        if channel_id == listen_together.RADIO_VC_ID:
            return self.voice_channel
        return None


class DummyBot:
    def __init__(self, *, music, radio, text_channel, voice_channel) -> None:
        self.music = music
        self.radio = radio
        self.text_channel = text_channel
        self.voice_channel = voice_channel
        self.user = SimpleNamespace(id=999)

    def get_cog(self, name: str):
        if name == "Music2Cog":
            return self.music
        if name == "RadioCog":
            return self.radio
        return None

    def get_channel(self, channel_id: int):
        if channel_id == listen_together.LISTEN_TOGETHER_CHANNEL_ID:
            return self.text_channel
        if channel_id == listen_together.RADIO_VC_ID:
            return self.voice_channel
        return None

    async def fetch_channel(self, channel_id: int):
        return self.get_channel(channel_id)


def make_active_session(*, title: str = "Du Ferme", listeners: int = 2, queued: int = 1):
    track = SimpleNamespace(
        title=title,
        uploader="La Fouine - Topic",
        requester_id=42,
    )
    music = SimpleNamespace(current=track, queue=[object() for _ in range(queued)])
    radio = SimpleNamespace(stream_url=None, voice=DummyVoice(playing=True))
    members = [DummyMember(index + 1) for index in range(listeners)]
    members.append(DummyMember(5000, bot=True))
    voice_channel = DummyVoiceChannel(listen_together.RADIO_VC_ID, members)
    text_channel = DummyTextChannel()
    bot = DummyBot(
        music=music,
        radio=radio,
        text_channel=text_channel,
        voice_channel=voice_channel,
    )
    return bot, music, radio, text_channel, voice_channel


@pytest.mark.asyncio
async def test_active_custom_music_creates_single_general_announcement() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    cog = listen_together.Music2ListenTogetherCog(bot)
    cog._initial_cleanup_done = True

    await cog._sync_announcement()
    await cog._sync_announcement()

    assert len(text_channel.sent) == 1
    embed = text_channel.sent[0]["embed"]
    assert embed.title == "🎧 Écoute ensemble"
    assert "Du Ferme" in embed.description
    assert "<@42>" in embed.description
    assert "**2** personne(s)" in embed.description
    assert "**1** titre(s)" in embed.description
    assert text_channel.message.edit.await_count == 0


@pytest.mark.asyncio
async def test_announcement_updates_when_track_or_listener_count_changes() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    cog = listen_together.Music2ListenTogetherCog(bot)
    cog._initial_cleanup_done = True

    await cog._sync_announcement()
    music.current = SimpleNamespace(
        title="Jefe",
        uploader="Ninho - Topic",
        requester_id=84,
    )
    voice_channel.members.append(DummyMember(33))

    await cog._sync_announcement()

    text_channel.message.edit.assert_awaited_once()
    embed = text_channel.message.edit.await_args.kwargs["embed"]
    assert "Jefe" in embed.description
    assert "<@84>" in embed.description
    assert "**3** personne(s)" in embed.description


@pytest.mark.asyncio
async def test_announcement_is_deleted_when_custom_music_ends() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    cog = listen_together.Music2ListenTogetherCog(bot)
    cog._initial_cleanup_done = True

    await cog._sync_announcement()
    music.current = None
    radio.stream_url = "https://radio.example/live"

    await cog._sync_announcement()

    text_channel.message.delete.assert_awaited_once()
    assert cog._message is None


@pytest.mark.asyncio
async def test_announcement_is_deleted_when_last_human_listener_leaves() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session(listeners=1)
    cog = listen_together.Music2ListenTogetherCog(bot)
    cog._initial_cleanup_done = True

    await cog._sync_announcement()
    voice_channel.members = [DummyMember(5000, bot=True)]

    await cog._sync_announcement()

    text_channel.message.delete.assert_awaited_once()
    assert cog._message is None


@pytest.mark.asyncio
async def test_active_radio_never_creates_listen_together_announcement() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    radio.stream_url = "https://radio.example/live"
    cog = listen_together.Music2ListenTogetherCog(bot)
    cog._initial_cleanup_done = True

    await cog._sync_announcement()

    assert text_channel.sent == []


@pytest.mark.asyncio
async def test_join_button_moves_member_from_another_voice_channel() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    cog = listen_together.Music2ListenTogetherCog(bot)
    other_channel = DummyVoiceChannel(123)
    member = DummyMember(77, channel=other_channel)
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=DummyGuild(voice_channel),
        user=member,
        response=response,
    )

    await cog.join_listening_session(interaction)

    member.move_to.assert_awaited_once_with(
        voice_channel,
        reason="Écoute ensemble Music 2.0",
    )
    response.send_message.assert_awaited_with(
        "🎧 Tu as rejoint l'écoute !",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_join_button_explains_discord_limit_when_member_is_disconnected() -> None:
    bot, music, radio, text_channel, voice_channel = make_active_session()
    cog = listen_together.Music2ListenTogetherCog(bot)
    member = DummyMember(77)
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=DummyGuild(voice_channel),
        user=member,
        response=response,
    )

    await cog.join_listening_session(interaction)

    member.move_to.assert_not_awaited()
    message = response.send_message.await_args.args[0]
    assert "Connecte-toi d'abord" in message
    assert f"<#{listen_together.RADIO_VC_ID}>" in message
