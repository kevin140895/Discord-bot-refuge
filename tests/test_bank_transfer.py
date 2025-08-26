from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import BankTransferModal
from storage import economy
from storage.transaction_store import TransactionStore


@pytest.mark.asyncio
async def test_bank_transfer_sends_dm(tmp_path, monkeypatch):
    tx_file = tmp_path / "transactions.json"
    store = TransactionStore(tx_file)
    monkeypatch.setattr(economy, "transactions", store)
    monkeypatch.setattr(economy_ui, "transactions", store)

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    modal = BankTransferModal(beneficiary_id=2)
    modal.amount = SimpleNamespace(value="200")

    recipient = SimpleNamespace(id=2, send=AsyncMock())
    client = SimpleNamespace(get_user=lambda _id: recipient)

    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, mention="@User"),
        guild=None,
        guild_id=0,
        client=client,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await modal.on_submit(interaction)

    add_xp_mock.assert_has_awaits(
        [
            call(1, amount=-200, guild_id=0, source="bank_transfer"),
            call(2, amount=200, guild_id=0, source="bank_transfer"),
        ]
    )
    txs = await store.all()
    assert txs[0]["type"] == "gift"
    assert txs[1]["type"] == "receive"
    response.send_message.assert_awaited_once()
    recipient.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_transfer_no_members():
    view = economy_ui.BankView()
    response = SimpleNamespace(send_message=AsyncMock())
    guild = SimpleNamespace(members=[SimpleNamespace(id=1)])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild=guild,
        response=response,
    )

    await view.open_transfer.callback(interaction)

    response.send_message.assert_awaited_once()
    args, kwargs = response.send_message.await_args_list[0]
    assert kwargs.get("ephemeral") is True
    assert "Aucun membre" in args[0]
