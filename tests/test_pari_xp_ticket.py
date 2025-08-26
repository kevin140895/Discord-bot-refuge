import importlib
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_loss_streak_grants_machine_ticket(tmp_path, monkeypatch):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    balance = {123: 1000}

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

    async def _get_channel():
        return None

    async def _get_announce_channel():
        return None

    cog._get_channel = _get_channel
    cog._get_announce_channel = _get_announce_channel
    cog._draw_segment = lambda: "lose_0x"

    class DummyResponse:
        async def send_message(self, *args, **kwargs):
            pass

    class DummyFollowup:
        def __init__(self):
            self.sent = []

        async def send(self, *args, **kwargs):
            content = kwargs.get("content") or kwargs.get("embed")
            if args:
                content = args[0]
            self.sent.append(content)

    class DummyInteraction:
        channel_id = 1
        channel = None
        guild_id = 0
        user = type("U", (), {"id": 123, "name": "Tester", "display_name": "Tester", "mention": "@Tester"})()
        response = DummyResponse()
        followup = DummyFollowup()
        data = {"components": [{"components": [{"custom_id": "pari_xp_amount", "value": "20"}]}]}

    interaction = DummyInteraction()

    for _ in range(10):
        cog._cooldowns = {}
        await pari_xp.RouletteRefugeCog._handle_bet_submission(cog, interaction)

    assert cog.roulette_store.has_ticket(str(interaction.user.id))
    assert any(
        isinstance(m, str) and "Ticket Gratuit" in m for m in interaction.followup.sent
    )

