import asyncio
from unittest.mock import patch
from pathlib import Path
import sys

import pytest
import discord
from discord.ext import commands

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.role_reminder import RoleReminderCog


class DummyTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


@pytest.mark.asyncio
async def test_cog_unload_cancels_tasks():
    created_tasks = []

    def fake_create_task(coro, *args, **kwargs):
        coro.close()
        t = DummyTask()
        created_tasks.append(t)
        return t

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())

    with patch("asyncio.create_task", fake_create_task):
        cog = RoleReminderCog(bot)

    await bot.add_cog(cog)
    await bot.remove_cog(cog.__cog_name__)

    assert all(t.cancelled for t in created_tasks)

    await bot.close()
