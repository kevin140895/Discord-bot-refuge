import asyncio
import importlib
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_bet_with_ticket(tmp_path, monkeypatch):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    tickets_file = tmp_path / "tickets.json"
    tx_file = tmp_path / "tx.json"
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(pari_xp, "TICKETS_PATH", str(tickets_file))
    monkeypatch.setattr(pari_xp, "TX_PATH", str(tx_file))
    monkeypatch.setattr(pari_xp, "STATE_PATH", str(state_file))

    balance: dict[int, int] = {123: 100}

    def fake_get_user_xp(uid: int) -> int:
        return balance.get(uid, 0)

    def fake_add_user_xp(uid: int, amount: int, reason: str = "") -> None:
        balance[uid] = balance.get(uid, 0) + amount

    monkeypatch.setattr(pari_xp, "get_user_xp", fake_get_user_xp)
    monkeypatch.setattr(pari_xp, "add_user_xp", fake_add_user_xp)
    monkeypatch.setattr(pari_xp, "get_user_account_age_days", lambda uid: 10)
    monkeypatch.setattr(pari_xp, "apply_double_xp_buff", lambda uid, minutes=60: None)

    tickets_file.write_text('[{"user_id": 123, "ts": "2024-01-01T00:00:00", "used": false}]')
    tx_file.write_text('[]')

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.bot = object()
    cog.config = {"channel_id": 1, "min_bet": 5, "daily_cap": 20, "min_balance_guard": 10}
    cog.state = {}
    cog._cooldowns = {}
    cog._bets_today = {}
    cog._bets_today_date = pari_xp.date.today()
    cog._now = lambda: pari_xp.datetime(2024, 1, 1, 12, 0, 0)
    cog._is_open_hours = lambda dt=None: True

    async def _get_channel():
        return None

    async def _ensure_leaderboard_message(channel):
        return None

    async def _get_announce_channel():
        return None

    cog._get_channel = _get_channel
    cog._ensure_leaderboard_message = _ensure_leaderboard_message
    cog._get_announce_channel = _get_announce_channel
    cog._build_leaderboard_embed = lambda: None
    cog._draw_segment = lambda: "lose_0x"

    class DummyResponse:
        async def send_message(self, *args, **kwargs):
            pass

    class DummyFollowup:
        async def send(self, *args, **kwargs):
            pass

    class DummyInteraction:
        channel_id = 1
        channel = None
        guild_id = 0
        user = type("U", (), {"id": 123, "name": "Tester", "display_name": "Tester", "mention": "@Tester"})()
        response = DummyResponse()
        followup = DummyFollowup()
        data = {
            "components": [
                {"components": [{"custom_id": "pari_xp_amount", "value": "20"}]},
                {"components": [{"custom_id": "pari_xp_use_ticket", "value": "oui"}]},
            ]
        }

    interaction = DummyInteraction()

    await pari_xp.RouletteRefugeCog._handle_bet_submission(cog, interaction)

    assert balance[123] == 100
    tickets = pari_xp.storage.load_json(pari_xp.storage.Path(pari_xp.TICKETS_PATH), [])
    assert tickets[0]["used"] is True
