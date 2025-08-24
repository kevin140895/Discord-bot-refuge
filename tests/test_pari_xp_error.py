import importlib
from pathlib import Path
import pytest


@pytest.mark.asyncio
async def test_handle_bet_submission_exception(tmp_path, monkeypatch):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    # Ensure temp files to avoid touching real data
    tx_file = tmp_path / "tx.json"
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(pari_xp, "TX_PATH", str(tx_file))
    monkeypatch.setattr(pari_xp, "STATE_PATH", str(state_file))

    balance = {123: 100}

    monkeypatch.setattr(pari_xp, "get_user_xp", lambda uid: balance.get(uid, 0))
    monkeypatch.setattr(pari_xp, "add_user_xp", lambda uid, amount, reason="": balance.__setitem__(uid, balance.get(uid, 0) + amount))
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

    def boom():
        raise RuntimeError("boom")

    cog._draw_segment = boom
    cog._compute_result = lambda amount, segment: {}

    sent = {"called": False, "kwargs": None}

    class DummyResponse:
        async def send_message(self, *args, **kwargs):
            pass

    class DummyFollowup:
        async def send(self, *args, **kwargs):
            sent["called"] = True
            sent["kwargs"] = kwargs

    class DummyInteraction:
        channel_id = 1
        channel = None
        guild_id = 0
        user = type("U", (), {"id": 123, "name": "Tester", "display_name": "Tester", "mention": "@Tester"})()
        response = DummyResponse()
        followup = DummyFollowup()
        data = {"components": [{"components": [{"custom_id": "pari_xp_amount", "value": "20"}]}]}

    interaction = DummyInteraction()

    logged = {"called": False}

    def fake_log(*args, **kwargs):
        logged["called"] = True

    monkeypatch.setattr(pari_xp.logging, "exception", fake_log)

    await pari_xp.RouletteRefugeCog._handle_bet_submission(cog, interaction)

    assert sent["called"] is True
    assert sent["kwargs"].get("ephemeral") is True
    assert logged["called"] is True
