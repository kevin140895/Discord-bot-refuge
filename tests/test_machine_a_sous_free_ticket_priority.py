import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cogs.machine_a_sous.machine_a_sous import MachineASousCog, MachineASousView
from storage.roulette_store import RouletteStore
import utils.economy_tickets as et
from utils.persist import atomic_write_json
from storage.transaction_store import TransactionStore
from utils.storage import load_json


@pytest.mark.asyncio
async def test_free_ticket_prioritized_over_store(tmp_path, monkeypatch):
    monkeypatch.setattr("cogs.machine_a_sous.machine_a_sous.is_open_now", lambda *a, **k: True)
    monkeypatch.setattr("cogs.machine_a_sous.machine_a_sous.DATA_DIR", str(tmp_path))

    ticket_path = tmp_path / "tickets.json"
    tx_path = tmp_path / "transactions.json"
    atomic_write_json(ticket_path, {"123": 1})
    et.TICKETS_FILE = ticket_path
    et.transactions = TransactionStore(tx_path)
    consume_mock = MagicMock(side_effect=et.consume_free_ticket)
    monkeypatch.setattr("cogs.machine_a_sous.machine_a_sous.consume_free_ticket", consume_mock)

    view = MachineASousView()
    flag = SimpleNamespace(free=None)

    async def fake_single_spin(self, interaction, cog, free=False):
        flag.free = free

    view._single_spin = fake_single_spin.__get__(view, MachineASousView)  # type: ignore

    bot = SimpleNamespace(wait_until_ready=asyncio.sleep)
    cog = MachineASousCog(bot)
    cog.store = RouletteStore(data_dir=str(tmp_path))
    uid = "123"
    cog.store.grant_ticket(uid)
    assert cog.store.has_ticket(uid)

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

    button = next(child for child in view.children if getattr(child, "custom_id", None) == "machineasous:play")
    await button.callback(interaction)
    await asyncio.sleep(0)

    assert flag.free is True
    assert cog.store.has_ticket(uid)
    assert load_json(ticket_path, {}) == {}
    consume_mock.assert_called_once()
