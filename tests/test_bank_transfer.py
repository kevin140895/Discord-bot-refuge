from types import SimpleNamespace
from unittest.mock import AsyncMock, call
import json

import pytest

import cogs.economy_ui as economy_ui
from storage import economy
from storage.transaction_store import TransactionStore
from storage.xp_store import XPStore


@pytest.mark.asyncio
async def test_bank_transfer_sends_dm(tmp_path, monkeypatch):
    tx_file = tmp_path / "transactions.json"
    store = TransactionStore(tx_file)
    monkeypatch.setattr(economy, "transactions", store)
    monkeypatch.setattr(economy_ui, "transactions", store)

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    recipient = SimpleNamespace(id=2, send=AsyncMock())
    client = SimpleNamespace(get_user=lambda _id: recipient)

    response = SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, mention="@User"),
        guild=None,
        guild_id=0,
        client=client,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await economy_ui._process_transfer(interaction, 2, "200")

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


@pytest.mark.asyncio
async def test_bank_transfer_uses_persisted_balance(tmp_path, monkeypatch):
    """The bank transfer should read the balance from disk if the XP store
    hasn't been initialised yet."""

    tx_file = tmp_path / "transactions.json"
    store = TransactionStore(tx_file)
    monkeypatch.setattr(economy, "transactions", store)
    monkeypatch.setattr(economy_ui, "transactions", store)

    # Prepare a lazy XP store with data for the sender but without loading it
    xp_file = tmp_path / "data.json"
    xp_file.write_text(json.dumps({"1": {"xp": 1000}}), encoding="utf-8")
    xp_store = XPStore(str(xp_file))
    monkeypatch.setattr(economy_ui.xp_adapter, "xp_store", xp_store)

    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    recipient = SimpleNamespace(id=2, send=AsyncMock())
    client = SimpleNamespace(get_user=lambda _id: recipient)
    response = SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, mention="@User"),
        guild=None,
        guild_id=0,
        client=client,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await economy_ui._process_transfer(interaction, 2, "200")

    add_xp_mock.assert_has_awaits(
        [
            call(1, amount=-200, guild_id=0, source="bank_transfer"),
            call(2, amount=200, guild_id=0, source="bank_transfer"),
        ]
    )
    response.send_message.assert_awaited_once()
    txs = await store.all()
    assert txs[0]["type"] == "gift"
    assert txs[1]["type"] == "receive"
