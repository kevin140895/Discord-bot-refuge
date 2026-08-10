import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

import cogs.streamer_temp_vc as streamer_temp_vc


class DummySnowflake:
    def __init__(self, snowflake_id: int) -> None:
        self.id = snowflake_id


class DummyVoiceChannel:
    def __init__(
        self,
        channel_id: int,
        *,
        name: str = "🔊・kevin",
        members=None,
        category=None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.members = list(members or [])
        self.category = category
        self.category_id = getattr(category, "id", None)
        self.mention = f"<#{channel_id}>"
        self.delete = AsyncMock()


class DummyCategory:
    def __init__(self, category_id: int, voice_channels=None) -> None:
        self.id = category_id
        self.voice_channels = list(voice_channels or [])


@pytest.fixture
def discord_channel_types(monkeypatch):
    monkeypatch.setattr(streamer_temp_vc.discord, "VoiceChannel", DummyVoiceChannel)
    monkeypatch.setattr(streamer_temp_vc.discord, "CategoryChannel", DummyCategory)


@pytest.mark.asyncio
async def test_ready_removes_missing_persisted_channel(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(
        streamer_temp_vc,
        "load_streamer_temp_vcs",
        lambda: {42: 7},
    )
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)

    bot = SimpleNamespace(get_channel=lambda _cid: None, guilds=[])
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog.on_ready()

    assert cog._channel_to_owner == {}
    assert cog._owner_to_channel == {}
    save_mock.assert_awaited_once_with({})


@pytest.mark.asyncio
async def test_ready_deletes_empty_untracked_streamer_channel(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(streamer_temp_vc, "load_streamer_temp_vcs", lambda: {})
    monkeypatch.setattr(
        streamer_temp_vc,
        "save_streamer_temp_vcs_async",
        AsyncMock(),
    )
    monkeypatch.setattr(streamer_temp_vc, "TEMP_VOICE_CATEGORY_ID", 123)

    category = DummyCategory(123)
    orphan = DummyVoiceChannel(55, category=category, members=[])
    category.voice_channels.append(orphan)
    guild = SimpleNamespace(
        get_channel=lambda cid: category if cid == category.id else None,
    )
    bot = SimpleNamespace(get_channel=lambda _cid: None, guilds=[guild])
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog.on_ready()

    orphan.delete.assert_awaited_once_with(
        reason="Salon streamer temporaire orphelin"
    )


@pytest.mark.asyncio
async def test_create_channel_persists_owner_mapping_and_permissions(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(streamer_temp_vc, "load_streamer_temp_vcs", lambda: {})
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)
    monkeypatch.setattr(streamer_temp_vc, "TEMP_VOICE_CATEGORY_ID", 0)
    monkeypatch.setattr(streamer_temp_vc, "TRIGGER_CHANNEL_ID", 0)

    category = DummyCategory(321)
    trigger = SimpleNamespace(category=category)
    role = DummySnowflake(streamer_temp_vc.ALLOWED_ROLE_ID)
    default_role = DummySnowflake(1)
    created = DummyVoiceChannel(99, category=category)

    guild = SimpleNamespace(
        default_role=default_role,
        get_role=lambda rid: role if rid == streamer_temp_vc.ALLOWED_ROLE_ID else None,
        get_member=lambda _mid: None,
        get_channel=lambda _cid: None,
        create_voice_channel=AsyncMock(return_value=created),
    )
    member = SimpleNamespace(
        id=7,
        display_name="Kevin",
        name="Kevin",
        guild=guild,
    )
    bot = SimpleNamespace(user=None)
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    channel = await cog._create_channel(member, trigger)

    assert channel is created
    assert cog._channel_to_owner == {99: 7}
    assert cog._owner_to_channel == {7: 99}
    save_mock.assert_awaited_once_with({99: 7})

    kwargs = guild.create_voice_channel.await_args.kwargs
    overwrites = kwargs["overwrites"]
    assert overwrites[default_role].view_channel is False
    assert overwrites[default_role].connect is False
    assert overwrites[role].view_channel is True
    assert overwrites[role].connect is True
    assert overwrites[role].speak is True
    assert overwrites[role].stream is True


@pytest.mark.asyncio
async def test_voice_lobby_creates_persists_and_moves_owner(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(streamer_temp_vc, "load_streamer_temp_vcs", lambda: {})
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)
    monkeypatch.setattr(streamer_temp_vc, "TEMP_VOICE_CATEGORY_ID", 0)
    monkeypatch.setattr(streamer_temp_vc, "TRIGGER_CHANNEL_ID", 0)

    category = DummyCategory(321)
    lobby = SimpleNamespace(
        id=streamer_temp_vc.STREAMER_LOBBY_VC_ID,
        category=category,
    )
    role = DummySnowflake(streamer_temp_vc.ALLOWED_ROLE_ID)
    default_role = DummySnowflake(1)
    created = DummyVoiceChannel(99, category=category)
    create_mock = AsyncMock(return_value=created)

    guild = SimpleNamespace(
        default_role=default_role,
        get_role=lambda rid: role if rid == streamer_temp_vc.ALLOWED_ROLE_ID else None,
        get_member=lambda _mid: None,
        get_channel=lambda _cid: None,
        create_voice_channel=create_mock,
    )
    member = SimpleNamespace(
        id=7,
        display_name="Kevin",
        name="Kevin",
        guild=guild,
        roles=[role],
        move_to=AsyncMock(),
    )
    bot = SimpleNamespace(user=None)
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=None),
        SimpleNamespace(channel=lobby),
    )

    create_mock.assert_awaited_once()
    member.move_to.assert_awaited_once_with(created)
    assert cog._channel_to_owner == {99: 7}
    assert cog._owner_to_channel == {7: 99}
    save_mock.assert_awaited_once_with({99: 7})


@pytest.mark.asyncio
async def test_voice_lobby_reuses_existing_owner_channel(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(
        streamer_temp_vc,
        "load_streamer_temp_vcs",
        lambda: {99: 7},
    )
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)

    category = DummyCategory(321)
    lobby = SimpleNamespace(
        id=streamer_temp_vc.STREAMER_LOBBY_VC_ID,
        category=category,
    )
    role = DummySnowflake(streamer_temp_vc.ALLOWED_ROLE_ID)
    existing = DummyVoiceChannel(99, category=category)
    create_mock = AsyncMock()

    guild = SimpleNamespace(
        get_role=lambda rid: role if rid == streamer_temp_vc.ALLOWED_ROLE_ID else None,
        get_channel=lambda cid: existing if cid == 99 else None,
        create_voice_channel=create_mock,
    )
    member = SimpleNamespace(
        id=7,
        display_name="Kevin",
        name="Kevin",
        guild=guild,
        roles=[role],
        move_to=AsyncMock(),
    )
    bot = SimpleNamespace(user=None)
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=None),
        SimpleNamespace(channel=lobby),
    )

    create_mock.assert_not_awaited()
    member.move_to.assert_awaited_once_with(existing)
    save_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_serializes_same_owner(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(streamer_temp_vc, "load_streamer_temp_vcs", lambda: {})
    monkeypatch.setattr(streamer_temp_vc, "TEMP_VOICE_CATEGORY_ID", 0)
    monkeypatch.setattr(streamer_temp_vc, "TRIGGER_CHANNEL_ID", 0)
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)

    category = DummyCategory(321)
    trigger = SimpleNamespace(category=category)
    role = DummySnowflake(streamer_temp_vc.ALLOWED_ROLE_ID)
    default_role = DummySnowflake(1)
    created = DummyVoiceChannel(99, category=category)
    channels = {}

    async def create_voice_channel(**_kwargs):
        await asyncio.sleep(0)
        channels[99] = created
        return created

    create_mock = AsyncMock(side_effect=create_voice_channel)
    guild = SimpleNamespace(
        default_role=default_role,
        get_role=lambda rid: role if rid == streamer_temp_vc.ALLOWED_ROLE_ID else None,
        get_member=lambda _mid: None,
        get_channel=lambda cid: channels.get(cid),
        create_voice_channel=create_mock,
    )
    member = SimpleNamespace(
        id=7,
        display_name="Kevin",
        name="Kevin",
        guild=guild,
    )
    cog = streamer_temp_vc.StreamerTempVCCog(SimpleNamespace(user=None))

    first, second = await asyncio.gather(
        cog._get_or_create_channel(member, trigger),
        cog._get_or_create_channel(member, trigger),
    )

    assert first == (created, True)
    assert second == (created, False)
    create_mock.assert_awaited_once()
    save_mock.assert_awaited_once_with({99: 7})


@pytest.mark.asyncio
async def test_voice_lobby_move_failure_deletes_new_channel_and_mapping(
    monkeypatch, discord_channel_types
):
    class DummyHTTPException(Exception):
        pass

    monkeypatch.setattr(streamer_temp_vc.discord, "HTTPException", DummyHTTPException)
    monkeypatch.setattr(streamer_temp_vc, "load_streamer_temp_vcs", lambda: {})
    monkeypatch.setattr(streamer_temp_vc, "TEMP_VOICE_CATEGORY_ID", 0)
    monkeypatch.setattr(streamer_temp_vc, "TRIGGER_CHANNEL_ID", 0)
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)

    category = DummyCategory(321)
    lobby = SimpleNamespace(
        id=streamer_temp_vc.STREAMER_LOBBY_VC_ID,
        category=category,
    )
    role = DummySnowflake(streamer_temp_vc.ALLOWED_ROLE_ID)
    default_role = DummySnowflake(1)
    created = DummyVoiceChannel(99, category=category)

    guild = SimpleNamespace(
        default_role=default_role,
        get_role=lambda rid: role if rid == streamer_temp_vc.ALLOWED_ROLE_ID else None,
        get_member=lambda _mid: None,
        get_channel=lambda _cid: None,
        create_voice_channel=AsyncMock(return_value=created),
    )
    member = SimpleNamespace(
        id=7,
        display_name="Kevin",
        name="Kevin",
        guild=guild,
        roles=[role],
        move_to=AsyncMock(side_effect=DummyHTTPException()),
    )
    cog = streamer_temp_vc.StreamerTempVCCog(SimpleNamespace(user=None))

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=None),
        SimpleNamespace(channel=lobby),
    )

    created.delete.assert_awaited_once_with(reason="Échec du déplacement du membre")
    assert cog._channel_to_owner == {}
    assert cog._owner_to_channel == {}
    assert save_mock.await_args_list == [call({99: 7}), call({})]


@pytest.mark.asyncio
async def test_delete_removes_mapping_only_after_success(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(
        streamer_temp_vc,
        "load_streamer_temp_vcs",
        lambda: {99: 7},
    )
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)
    monkeypatch.setattr(streamer_temp_vc, "DELETE_DELAY_SECONDS", 0)

    channel = DummyVoiceChannel(99, members=[])
    bot = SimpleNamespace(get_channel=lambda cid: channel if cid == 99 else None)
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog._delete_after_delay(99)

    channel.delete.assert_awaited_once_with(reason="Salon temporaire vide")
    assert cog._channel_to_owner == {}
    assert cog._owner_to_channel == {}
    save_mock.assert_awaited_once_with({})


@pytest.mark.asyncio
async def test_delete_keeps_mapping_when_channel_reoccupied(
    monkeypatch, discord_channel_types
):
    monkeypatch.setattr(
        streamer_temp_vc,
        "load_streamer_temp_vcs",
        lambda: {99: 7},
    )
    save_mock = AsyncMock()
    monkeypatch.setattr(streamer_temp_vc, "save_streamer_temp_vcs_async", save_mock)
    monkeypatch.setattr(streamer_temp_vc, "DELETE_DELAY_SECONDS", 0)

    channel = DummyVoiceChannel(99, members=[object()])
    bot = SimpleNamespace(get_channel=lambda cid: channel if cid == 99 else None)
    cog = streamer_temp_vc.StreamerTempVCCog(bot)

    await cog._delete_after_delay(99)

    channel.delete.assert_not_awaited()
    assert cog._channel_to_owner == {99: 7}
    assert cog._owner_to_channel == {7: 99}
    save_mock.assert_not_awaited()
