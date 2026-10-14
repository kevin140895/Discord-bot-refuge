from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import cogs.first_message as fm
from config import GENERAL_CHANNEL_ID


def test_daily_reset_is_scheduled_at_8am_paris():
    times = fm.FirstMessageCog.daily_reset.time

    assert times is not None
    assert len(times) == 1
    scheduled = times[0]
    assert (scheduled.hour, scheduled.minute, scheduled.second) == (8, 0, 0)
    assert scheduled.tzinfo is fm.PARIS_TZ


def _make_message(channel_id=GENERAL_CHANNEL_ID):
    return SimpleNamespace(
        id=456,
        author=SimpleNamespace(bot=False, id=1, mention="@user"),
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=channel_id, send=AsyncMock()),
        created_at=datetime(2025, 1, 1, 9, 0),
        add_reaction=AsyncMock(),
    )


def _make_cog(monkeypatch, tmp_path):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 1, 1, 9, 0, tzinfo=tz)

    monkeypatch.setattr(fm, "datetime", FixedDatetime)
    monkeypatch.setattr(fm, "FIRST_WIN_FILE", str(tmp_path / "first_win.json"))
    monkeypatch.setattr(fm.FirstMessageCog, "_save_state", AsyncMock())

    async def wait_until_ready():
        return None

    bot = SimpleNamespace(wait_until_ready=wait_until_ready)
    with patch.object(fm.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = fm.FirstMessageCog(bot)
    cog.first_message_claimed = False
    return cog


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [GENERAL_CHANNEL_ID, 987654321])
async def test_first_message_awards_xp_once_and_reacts_without_announcement(
    tmp_path, monkeypatch, channel_id
):
    add_xp = AsyncMock(return_value=(0, 0, 0, 400))
    record = AsyncMock()
    monkeypatch.setattr(fm.xp_store, "add_xp", add_xp)
    monkeypatch.setattr(fm.season_store, "record", record)

    cog = _make_cog(monkeypatch, tmp_path)
    message = _make_message(channel_id)

    await cog.on_message(message)
    await cog.on_message(message)  # Un seul vainqueur et une seule réaction.

    add_xp.assert_awaited_once_with(1, 400, guild_id=123, source="message")
    record.assert_awaited_once_with(1, at=message.created_at, xp_earned=400)
    message.add_reaction.assert_awaited_once_with("🏆")
    message.channel.send.assert_not_awaited()
    assert cog.winner_id == 1
    assert cog.first_message_claimed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type, status",
    [
        (fm.discord.Forbidden, 403),
        (fm.discord.NotFound, 404),
        (fm.discord.HTTPException, 500),
    ],
)
async def test_failed_reaction_keeps_xp_and_does_not_send_message(
    tmp_path, monkeypatch, error_type, status
):
    add_xp = AsyncMock(return_value=(0, 0, 0, 400))
    record = AsyncMock()
    monkeypatch.setattr(fm.xp_store, "add_xp", add_xp)
    monkeypatch.setattr(fm.season_store, "record", record)

    cog = _make_cog(monkeypatch, tmp_path)
    message = _make_message(987654321)
    response = SimpleNamespace(status=status, reason="Reaction unavailable")
    message.add_reaction.side_effect = error_type(response, "Reaction unavailable")

    await cog.on_message(message)
    await cog.on_message(message)  # Un refus Discord ne doit jamais réattribuer les XP.

    add_xp.assert_awaited_once_with(1, 400, guild_id=123, source="message")
    record.assert_awaited_once_with(1, at=message.created_at, xp_earned=400)
    message.add_reaction.assert_awaited_once_with("🏆")
    message.channel.send.assert_not_awaited()
    assert cog.winner_id == 1
    assert cog.first_message_claimed is True
    assert fm.FirstMessageCog._save_state.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ignored_case", ["bot", "dm", "before_8"])
async def test_ineligible_messages_do_not_trigger_reward_or_reaction(
    tmp_path, monkeypatch, ignored_case
):
    add_xp = AsyncMock()
    monkeypatch.setattr(fm.xp_store, "add_xp", add_xp)
    cog = _make_cog(monkeypatch, tmp_path)
    message = _make_message()

    if ignored_case == "bot":
        message.author.bot = True
    elif ignored_case == "dm":
        message.guild = None
    else:
        class EarlyDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2025, 1, 1, 7, 59, tzinfo=tz)

        monkeypatch.setattr(fm, "datetime", EarlyDatetime)

    await cog.on_message(message)

    add_xp.assert_not_awaited()
    message.add_reaction.assert_not_awaited()
    message.channel.send.assert_not_awaited()
