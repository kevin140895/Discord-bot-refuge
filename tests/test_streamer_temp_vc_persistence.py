from types import SimpleNamespace
from unittest.mock import AsyncMock

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
async def test_create_channel_persists_owner_mapping(
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
