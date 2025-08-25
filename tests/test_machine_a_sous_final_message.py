import os
import sys
from pathlib import Path

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousView


@pytest.mark.asyncio
async def test_single_spin_shows_gain(monkeypatch):
    # Avoid real sleep and randomness
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr("random.choices", lambda *a, **k: [20])
    monkeypatch.setattr(
        "utils.discord_utils.limiter", SimpleNamespace(acquire=AsyncMock())
    )

    message = SimpleNamespace(
        content="",
        embeds=[SimpleNamespace(to_dict=lambda: {})],
        edit=AsyncMock(),
    )

    class DummyFollowup:
        async def send(self, *args, **kwargs):
            return message

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, mention="@user"),
        guild=None,
        guild_id=1,
        followup=DummyFollowup(),
    )

    view = MachineASousView()

    async def fake_reward_xp_gain(self, interaction, cog, gain, free):
        return (f"🎰 Résultat : **{gain} XP**.", False, None, 0, 0, 0, 0)

    view._reward_xp_gain = fake_reward_xp_gain.__get__(view, MachineASousView)

    cog = SimpleNamespace(bot=SimpleNamespace(), store=SimpleNamespace())

    await view._single_spin(interaction, cog)

    message.edit.assert_awaited_once()
    assert "🎰 Résultat" in message.edit.await_args.kwargs["content"]
    assert message.edit.await_args.kwargs["embed"] is None
