from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.pari_xp as pari_xp


def _view_text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _living_cog(living_state: dict[str, object]):
    return SimpleNamespace(
        is_open=True,
        living_state=living_state,
        state={
            "is_open": True,
            "total_bets": 12345,
            "total_winnings": 6789,
            "last_winner": {"user_id": 99, "amount": 20},
        },
    )


def test_living_panel_renders_recent_streak_spotlight_and_biggest_win():
    cog = _living_cog(
        {
            "recent": [
                {
                    "id": 4,
                    "bet_type": "number",
                    "selected_number": 17,
                    "drawn_number": 23,
                    "won": False,
                    "zero_hit": False,
                },
                {
                    "id": 3,
                    "bet_type": "red",
                    "won": True,
                    "zero_hit": False,
                },
                {
                    "id": 2,
                    "bet_type": "black",
                    "won": False,
                    "zero_hit": False,
                },
                {
                    "id": 1,
                    "bet_type": "even",
                    "won": False,
                    "zero_hit": True,
                },
            ],
            "spotlight": {
                "user_id": 42,
                "bets": 7,
                "wins": 4,
                "net_xp": 320,
            },
            "biggest_win": {
                "id": 10,
                "user_id": 77,
                "bet_type": "number",
                "payout_xp": 1000,
            },
            "streak": {"side": "house", "count": 3},
        }
    )

    view = pari_xp.RouletteXPView(cog)
    text = _view_text(view)

    assert "### 🎰 Vie des tables" in text
    assert "🎯 17 → 23 · 🏛️ Maison" in text
    assert "🔴 Rouge · 👑 gagné" in text
    assert "🟢 Zéro Vert · 🏛️ Maison" in text
    assert "🏛️ Série : **3 coups pour la Maison**" in text
    assert "### 🔥 Joueur en vue · 24 h" in text
    assert "<@42> · **+320 XP net** · 4 gains / 7 paris" in text
    assert "### 💎 Plus gros gain · 24 h" in text
    assert "<@77> · **1 000 XP** · 🎯 Numéro" in text
    assert "Dernier gagnant" not in text
    assert "%" not in text
    assert "probabilit" not in text.lower()


def test_living_panel_keeps_last_winner_fallback_before_history_exists():
    cog = _living_cog(pari_xp._empty_living_state())

    text = _view_text(pari_xp.RouletteXPView(cog))

    assert "Vie des tables" not in text
    assert "Joueur en vue" not in text
    assert "Plus gros gain" not in text
    assert "### 👑 Dernier gagnant" in text
    assert "<@99> a remporté **20 XP**" in text


def test_recent_event_formatter_never_invents_a_number_for_simple_bets():
    assert (
        pari_xp._format_recent_event(
            {
                "bet_type": "odd",
                "won": True,
                "zero_hit": False,
                "drawn_number": 31,
            }
        )
        == "◼️ Impair · 👑 gagné"
    )


@pytest.mark.asyncio
async def test_record_living_event_persists_then_refreshes(monkeypatch):
    store = SimpleNamespace(
        record_event=AsyncMock(return_value=123),
        get_living_snapshot=AsyncMock(
            return_value={
                "recent": [{"id": 123}],
                "spotlight": None,
                "biggest_win": None,
                "streak": None,
            }
        ),
    )
    monkeypatch.setattr(pari_xp, "roulette_history_store", store)

    cog = object.__new__(pari_xp.PariXPCog)
    cog.tz = pari_xp.PARIS_TZ
    cog.living_state = pari_xp._empty_living_state()

    await cog._record_living_event(
        user_id=42,
        bet_type="number",
        amount=10,
        payout=100,
        win=True,
        zero_hit=False,
        selected_number=8,
        drawn_number=8,
    )

    store.record_event.assert_awaited_once()
    kwargs = store.record_event.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["bet_type"] == "number"
    assert kwargs["wager_xp"] == 10
    assert kwargs["payout_xp"] == 100
    assert kwargs["won"] is True
    assert kwargs["selected_number"] == 8
    assert kwargs["drawn_number"] == 8
    store.get_living_snapshot.assert_awaited_once_with(
        recent_limit=pari_xp.LIVING_RECENT_LIMIT,
        window_hours=24,
    )
    assert cog.living_state["recent"] == [{"id": 123}]
