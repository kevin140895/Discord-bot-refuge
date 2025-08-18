import sys
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import discord
from discord.ext import commands

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.role_reminder import RoleReminderCog, REMINDER_CHANNEL_ID


class DummyTask:
    def cancel(self):
        pass


@pytest.mark.asyncio
async def test_no_duplicate_reminder_before_ttl():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())

    def fake_create_task(coro, *args, **kwargs):
        coro.close()
        return DummyTask()

    channel = SimpleNamespace(
        id=REMINDER_CHANNEL_ID,
        send=AsyncMock(),
        permissions_for=lambda member: SimpleNamespace(
            send_messages=True,
            manage_messages=True,
            read_message_history=True,
        ),
    )

    member = SimpleNamespace(id=123, mention="@member", bot=False)

    guild = SimpleNamespace(
        id=456,
        members=[member],
        get_channel=lambda channel_id: channel if channel_id == REMINDER_CHANNEL_ID else None,
        me=SimpleNamespace(id=999),
        get_member=lambda _id: None,
    )

    with patch("asyncio.create_task", fake_create_task), \
         patch("cogs.role_reminder.user_without_chosen_role", return_value=True), \
         patch.object(RoleReminderCog, "_save_state"):
        cog = RoleReminderCog(bot)

    cog.reminders[str(guild.id)] = {
        str(member.id): {
            "message_id": 1,
            "channel_id": channel.id,
            "created_at": datetime.now(),
        }
    }

    await cog._run_scan_once(guild=guild)

    channel.send.assert_not_awaited()

    await bot.close()
