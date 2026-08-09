from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import cogs.pari_xp as pari_xp
from ui.casino_views import CasinoLeaderboardView


def _make_cog(state: dict):
    cog = object.__new__(pari_xp.PariXPCog)
    cog.bot = SimpleNamespace(fetch_user=AsyncMock())
    cog.tz = pari_xp.PARIS_TZ
    cog.state = state
    cog.is_open = True
    cog._message_id = None
    cog._last_announced_state = None
    cog._last_panel_signature = None
    return cog


def _view_text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


@pytest.mark.asyncio
async def test_completed_bets_update_per_player_casino_stats(monkeypatch):
    cog = _make_cog({"is_open": True, "total_bets": 0, "total_winnings": 0})
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", AsyncMock())
    monkeypatch.setattr(
        pari_xp,
        "award_xp",
        AsyncMock(return_value=(1, 1, 80, 120)),
    )
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.10)
    monkeypatch.setattr(pari_xp.asyncio, "sleep", AsyncMock())

    result_message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
        response=SimpleNamespace(send_message=AsyncMock()),
        original_response=AsyncMock(return_value=result_message),
    )

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "red", 20)

    assert cog.state["players"]["42"] == {
        "bets": 1,
        "wagered": 20,
        "winnings": 40,
    }


@pytest.mark.asyncio
async def test_losing_bet_records_stake_without_winnings(monkeypatch):
    cog = _make_cog({"is_open": True, "total_bets": 0, "total_winnings": 0})
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", AsyncMock())
    monkeypatch.setattr(pari_xp, "award_xp", AsyncMock())
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.90)
    monkeypatch.setattr(pari_xp.asyncio, "sleep", AsyncMock())

    result_message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
        response=SimpleNamespace(send_message=AsyncMock()),
        original_response=AsyncMock(return_value=result_message),
    )

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "black", 20)

    assert cog.state["players"]["42"] == {
        "bets": 1,
        "wagered": 20,
        "winnings": 0,
    }


@pytest.mark.asyncio
async def test_top_casino_ranks_net_casino_result_not_global_xp(monkeypatch):
    cog = _make_cog(
        {
            "players": {
                "1": {"bets": 2, "wagered": 100, "winnings": 120},
                "2": {"bets": 1, "wagered": 10, "winnings": 100},
                "3": {"bets": 4, "wagered": 500, "winnings": 550},
            }
        }
    )
    monkeypatch.setattr(
        pari_xp.xp_store,
        "read_json",
        Mock(side_effect=AssertionError("global XP must not drive top_casino")),
    )

    names = {1: "Alice", 2: "Bob", 3: "Charlie"}
    guild = SimpleNamespace(
        get_member=lambda uid: SimpleNamespace(display_name=names[uid]),
    )
    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await pari_xp.PariXPCog.top_casino.callback(cog, interaction)

    kwargs = interaction.response.send_message.await_args.kwargs
    view = kwargs["view"]
    assert isinstance(view, CasinoLeaderboardView)
    assert "embed" not in kwargs

    description = _view_text(view)
    assert description.index("Bob") < description.index("Charlie") < description.index("Alice")
    assert "**+90 XP net**" in description
    assert "Classement par **résultat net**" in description


@pytest.mark.asyncio
async def test_top_casino_does_not_fallback_to_global_xp(monkeypatch):
    cog = _make_cog({"players": {}})
    monkeypatch.setattr(
        pari_xp.xp_store,
        "read_json",
        Mock(side_effect=AssertionError("global XP must not be read")),
    )
    interaction = SimpleNamespace(
        guild=None,
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await pari_xp.PariXPCog.top_casino.callback(cog, interaction)

    kwargs = interaction.response.send_message.await_args.kwargs
    view = kwargs["view"]
    assert isinstance(view, CasinoLeaderboardView)
    assert "Aucune activité casino enregistrée pour le moment." in _view_text(view)
    assert kwargs["ephemeral"] is True
