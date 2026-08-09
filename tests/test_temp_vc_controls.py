from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.temp_vc_controls as controls


class DummyPermissions:
    def __init__(self, *, manage_channels: bool = False) -> None:
        self.manage_channels = manage_channels


class DummyOverwrite:
    def __init__(self) -> None:
        self.connect = None
        self.view_channel = None
        self.speak = None


class DummyVoiceChannel:
    def __init__(
        self,
        channel_id: int,
        *,
        name: str = "PC • Fortnite",
        members=None,
        category_id: int = 500,
    ):
        self.id = channel_id
        self.name = name
        self.members = list(members or [])
        self.category_id = category_id
        self.user_limit = 0
        self.set_permissions = AsyncMock()
        self.edit = AsyncMock()
        self.send = AsyncMock()

    def overwrites_for(self, _target):
        return DummyOverwrite()


class DummyMember:
    def __init__(
        self,
        member_id: int,
        *,
        channel=None,
        manage_channels: bool = False,
        bot: bool = False,
    ) -> None:
        self.id = member_id
        self.bot = bot
        self.mention = f"<@{member_id}>"
        self.guild_permissions = DummyPermissions(manage_channels=manage_channels)
        self.voice = SimpleNamespace(channel=channel) if channel is not None else None
        self.move_to = AsyncMock()


class DummyResponse:
    def __init__(self) -> None:
        self.send_message = AsyncMock()
        self.send_modal = AsyncMock()


@pytest.fixture
def discord_types(monkeypatch):
    monkeypatch.setattr(controls.discord, "VoiceChannel", DummyVoiceChannel)
    monkeypatch.setattr(controls.discord, "Member", DummyMember)


@pytest.mark.asyncio
async def test_standard_lobby_move_records_owner_without_touching_name(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "LOBBY_VC_ID", 10)
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    channel = DummyVoiceChannel(99, name="PC • Fortnite")
    bot = SimpleNamespace(get_channel=lambda cid: channel if cid == 99 else None)
    cog = controls.TempVCControlsCog(bot)
    member = DummyMember(7, channel=channel)

    before = SimpleNamespace(channel=SimpleNamespace(id=10))
    after = SimpleNamespace(channel=channel)
    await cog.on_voice_state_update(member, before, after)

    assert cog.owner_id(99) == 7
    assert channel.name == "PC • Fortnite"
    channel.edit.assert_not_awaited()
    save.assert_awaited_once_with({99: 7})


@pytest.mark.asyncio
async def test_non_lobby_join_does_not_claim_unowned_channel(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "LOBBY_VC_ID", 10)
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    channel = DummyVoiceChannel(99)
    cog = controls.TempVCControlsCog(SimpleNamespace(get_channel=lambda _cid: channel))
    member = DummyMember(8, channel=channel)

    await cog.on_voice_state_update(
        member,
        SimpleNamespace(channel=SimpleNamespace(id=123)),
        SimpleNamespace(channel=channel),
    )

    assert cog.owner_id(99) is None
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_changes_permissions_not_dynamic_name(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", AsyncMock())

    channel = DummyVoiceChannel(99, name="Crossplay • Call of Duty")
    owner = DummyMember(7, channel=channel)
    default_role = object()
    guild = SimpleNamespace(default_role=default_role)
    interaction = SimpleNamespace(
        user=owner,
        guild=guild,
        response=DummyResponse(),
    )
    cog = controls.TempVCControlsCog(SimpleNamespace())

    await cog.set_locked(interaction, True)

    assert channel.name == "Crossplay • Call of Duty"
    channel.edit.assert_not_awaited()
    channel.set_permissions.assert_awaited_once()
    overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
    assert overwrite.connect is False


@pytest.mark.asyncio
async def test_limit_updates_only_user_limit(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", AsyncMock())

    channel = DummyVoiceChannel(99, name="PC • Rocket League")
    owner = DummyMember(7, channel=channel)
    interaction = SimpleNamespace(
        user=owner,
        guild=SimpleNamespace(default_role=object()),
        response=DummyResponse(),
    )
    cog = controls.TempVCControlsCog(SimpleNamespace())

    await cog.set_user_limit(interaction, "5")

    assert channel.name == "PC • Rocket League"
    channel.edit.assert_awaited_once()
    assert channel.edit.await_args.kwargs["user_limit"] == 5
    assert "name" not in channel.edit.await_args.kwargs


@pytest.mark.asyncio
async def test_claim_rejected_while_owner_is_present(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    owner = DummyMember(7)
    claimant = DummyMember(8)
    channel = DummyVoiceChannel(99, members=[owner, claimant])
    claimant.voice = SimpleNamespace(channel=channel)
    interaction = SimpleNamespace(
        user=claimant,
        guild=SimpleNamespace(default_role=object()),
        response=DummyResponse(),
    )
    cog = controls.TempVCControlsCog(SimpleNamespace())

    await cog.claim(interaction)

    assert cog.owner_id(99) == 7
    save.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_succeeds_after_owner_leaves(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    claimant = DummyMember(8)
    channel = DummyVoiceChannel(99, members=[claimant])
    claimant.voice = SimpleNamespace(channel=channel)
    interaction = SimpleNamespace(
        user=claimant,
        guild=SimpleNamespace(default_role=object()),
        response=DummyResponse(),
    )
    cog = controls.TempVCControlsCog(SimpleNamespace())

    await cog.claim(interaction)

    assert cog.owner_id(99) == 8
    save.assert_awaited_once_with({99: 8})


@pytest.mark.asyncio
async def test_transfer_requires_target_in_same_channel(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    channel = DummyVoiceChannel(99)
    owner = DummyMember(7, channel=channel)
    target = DummyMember(8)
    channel.members = [owner]
    interaction = SimpleNamespace(
        user=owner,
        guild=SimpleNamespace(default_role=object()),
        response=DummyResponse(),
    )
    cog = controls.TempVCControlsCog(SimpleNamespace())

    await cog.transfer_owner(interaction, target)

    assert cog.owner_id(99) == 7
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_channel_cleans_owner_mapping(
    monkeypatch, discord_types
):
    monkeypatch.setattr(controls, "TEMP_VC_CATEGORY", 500)
    monkeypatch.setattr(controls, "load_temp_vc_owners", lambda: {99: 7})
    save = AsyncMock()
    monkeypatch.setattr(controls, "save_temp_vc_owners_async", save)

    cog = controls.TempVCControlsCog(SimpleNamespace())
    await cog.on_guild_channel_delete(SimpleNamespace(id=99))

    assert cog.owner_id(99) is None
    save.assert_awaited_once_with({})
