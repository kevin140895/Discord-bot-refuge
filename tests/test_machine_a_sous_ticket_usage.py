import asyncio
from types import SimpleNamespace
from pathlib import Path
import os

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousCog, MachineASousView, PARIS_TZ
from storage.roulette_store import RouletteStore


@pytest.mark.asyncio
async def test_ticket_can_be_used_before_daily_spin(monkeypatch, tmp_path):
    # Ensure machine is considered open and use a temp data dir
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.is_open_now", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.DATA_DIR", str(tmp_path)
    )

    # Stub _single_spin to capture the 'free' flag
    view = MachineASousView()
    flag = SimpleNamespace(free=None)

    async def fake_single_spin(self, interaction, cog, free=False):
        flag.free = free

    # Bind the fake method to our view
    view._single_spin = fake_single_spin.__get__(view, MachineASousView)  # type: ignore

    # Prepare a cog with a store pointing to a temporary directory
    bot = SimpleNamespace(wait_until_ready=asyncio.sleep)
    cog = MachineASousCog(bot)
    cog.store = RouletteStore(data_dir=str(tmp_path))

    uid = "123"
    cog.store.grant_ticket(uid)
    assert cog.store.has_ticket(uid)

    # Minimal interaction stub
    class DummyResponse:
        async def defer(self, **kwargs):
            pass

        async def send_message(self, *args, **kwargs):
            pass

    class DummyFollowup:
        async def send(self, *args, **kwargs):
            pass

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=int(uid)),
        guild=SimpleNamespace(),
        guild_id=1,
        client=SimpleNamespace(get_cog=lambda name: cog),
        response=DummyResponse(),
        followup=DummyFollowup(),
    )

    # Trigger the button callback
    button = next(
        child for child in view.children if getattr(child, "custom_id", None) == "machineasous:play"
    )
    await button.callback(interaction)

    # Ticket was used for a free spin and daily claim remains untouched
    assert flag.free is True
    assert not cog.store.has_claimed_today(uid, tz=PARIS_TZ)
    assert not cog.store.has_ticket(uid)
