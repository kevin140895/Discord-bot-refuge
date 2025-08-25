import asyncio
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from utils.storage import load_json


@pytest.mark.asyncio
async def test_pari_xp_uses_free_ticket(tmp_path, monkeypatch):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")
    economy_tickets = importlib.import_module("utils.economy_tickets")
    consume_mock = MagicMock(side_effect=pari_xp.consume_free_ticket)
    monkeypatch.setattr(pari_xp, "consume_free_ticket", consume_mock)

    ticket_path = tmp_path / "tickets.json"
    tx_path = tmp_path / "transactions.json"
    economy_tickets.TICKETS_FILE = ticket_path
    from storage.transaction_store import TransactionStore
    economy_tickets.transactions = TransactionStore(tx_path)
    # give free ticket
    from utils.persist import atomic_write_json
    atomic_write_json(ticket_path, {"123": 1})

    balance = {123: 100}

    def fake_get_user_xp(uid: int) -> int:
        return balance.get(uid, 0)

    def fake_add_user_xp(uid: int, amount: int, guild_id: int = 0, source: str = "") -> None:
        balance[uid] = balance.get(uid, 0) + amount

    monkeypatch.setattr(pari_xp, "get_user_xp", fake_get_user_xp)
    monkeypatch.setattr(pari_xp, "add_user_xp", fake_add_user_xp)
    monkeypatch.setattr(pari_xp, "get_user_account_age_days", lambda uid: 10)
    monkeypatch.setattr(pari_xp, "apply_double_xp_buff", lambda uid, minutes=60: None)

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.bot = object()
    cog.config = {"channel_id": 1, "min_bet": 5, "daily_cap": 20, "min_balance_guard": 10}
    cog.state = {}
    cog._cooldowns = {}
    cog._bets_today = {}
    cog._bets_today_date = pari_xp.date.today()
    cog._now = lambda: pari_xp.datetime(2024, 1, 1, 12, 0, 0)
    cog._is_open_hours = lambda dt=None: True
    cog.roulette_store = pari_xp.RouletteStore(data_dir=str(tmp_path))
    cog._loss_streak = {}
    cog._draw_segment = lambda: "win_2x"

    class DummyResponse:
        async def send_message(self, *args, **kwargs):
            pass

    class DummyFollowup:
        async def send(self, *args, **kwargs):
            pass

    class DummyInteraction:
        channel_id = 1
        guild_id = 0
        user = type("U", (), {"id": 123, "name": "Tester", "display_name": "Tester", "mention": "@Tester"})()
        response = DummyResponse()
        followup = DummyFollowup()
        data = {"components": [{"components": [{"custom_id": "pari_xp_amount", "value": "20"}]}]}

    interaction = DummyInteraction()

    await pari_xp.RouletteRefugeCog._handle_bet_submission(cog, interaction)
    await asyncio.sleep(0)
    assert balance[123] == 140
    assert load_json(ticket_path, {}) == {}
    consume_mock.assert_called_once()
