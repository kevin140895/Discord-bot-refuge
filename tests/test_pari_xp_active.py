import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.pari_xp as pari_xp


def _make_cog(*, is_open: bool = True):
    cog = object.__new__(pari_xp.PariXPCog)
    cog.bot = object()
    cog.tz = pari_xp.PARIS_TZ
    cog.state = {"is_open": is_open, "total_bets": 0, "total_winnings": 0}
    cog.is_open = is_open
    cog._message_id = None
    cog._last_announced_state = None
    cog._last_panel_signature = None
    cog._bet_lock = asyncio.Lock()
    return cog


def _view_text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_is_open_now_respects_overnight_schedule(monkeypatch):
    monkeypatch.setattr(pari_xp, "CASINO_OPEN_HOUR", 10)
    monkeypatch.setattr(pari_xp, "CASINO_CLOSE_HOUR", 6)
    cog = _make_cog()

    assert cog._is_open_now(pari_xp.datetime(2026, 8, 8, 10, 0, tzinfo=pari_xp.PARIS_TZ))
    assert cog._is_open_now(pari_xp.datetime(2026, 8, 9, 5, 59, tzinfo=pari_xp.PARIS_TZ))
    assert not cog._is_open_now(pari_xp.datetime(2026, 8, 8, 9, 59, tzinfo=pari_xp.PARIS_TZ))
    assert not cog._is_open_now(pari_xp.datetime(2026, 8, 9, 6, 0, tzinfo=pari_xp.PARIS_TZ))


def test_simple_bet_roll_boundaries_use_40_percent_win_band():
    cutoff = pari_xp.HOUSE_ZERO_CHANCE + pari_xp.SIMPLE_BET_WIN_CHANCE

    assert pari_xp._resolve_simple_bet_roll(0.0) == (True, False)
    assert pari_xp._resolve_simple_bet_roll(0.029999) == (True, False)
    assert pari_xp._resolve_simple_bet_roll(0.03) == (False, True)
    assert pari_xp._resolve_simple_bet_roll(cutoff - 1e-9) == (False, True)
    assert pari_xp._resolve_simple_bet_roll(cutoff) == (False, False)
    assert pari_xp._resolve_simple_bet_roll(0.999999) == (False, False)


@pytest.mark.parametrize("roll", [-0.01, 1.0])
def test_simple_bet_roll_rejects_invalid_values(roll):
    with pytest.raises(ValueError):
        pari_xp._resolve_simple_bet_roll(roll)


def test_roulette_v2_reflects_refuge_royal_active_state(monkeypatch):
    monkeypatch.setattr(pari_xp, "CASINO_OPEN_HOUR", 10)
    monkeypatch.setattr(pari_xp, "CASINO_CLOSE_HOUR", 6)
    monkeypatch.setattr(pari_xp, "CASINO_SCHEDULE_LABEL", "10h00 - 06h00")
    cog = _make_cog(is_open=True)
    cog.state["total_bets"] = 250
    cog.state["total_winnings"] = 100
    cog.state["last_winner"] = {"user_id": 42, "amount": 500}

    view = pari_xp.RouletteXPView(cog)
    text = _view_text(view)

    assert isinstance(view, discord.ui.LayoutView)
    assert "👑 Casino du Refuge" in text
    assert "Maison Royale · Roulette XP" in text
    assert "🟢 **OUVERT**" in text
    assert "fermeture à **06:00**" in text
    assert "Tables royales" in text
    assert "gain x2" in text
    assert "gain x10" in text
    assert "Zéro Vert" in text
    assert "XP misés : **250**" in text
    assert "XP redistribués : **100**" in text
    assert "<@42> a remporté **500 XP**" in text
    assert "%" not in text
    assert "probabilit" not in text.lower()

    buttons = {
        item.custom_id: item.label
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    }
    assert buttons == {
        "pari_xp:red": "🔴 Rouge · x2",
        "pari_xp:black": "⚫ Noir · x2",
        "pari_xp:even": "⚪ Pair · x2",
        "pari_xp:odd": "◼️ Impair · x2",
        "pari_xp:number": "🎯 Numéro · x10",
    }


def test_roulette_v2_reflects_refuge_royal_closed_state(monkeypatch):
    monkeypatch.setattr(pari_xp, "CASINO_OPEN_HOUR", 10)
    monkeypatch.setattr(pari_xp, "CASINO_CLOSE_HOUR", 6)
    monkeypatch.setattr(pari_xp, "CASINO_SCHEDULE_LABEL", "10h00 - 06h00")
    cog = _make_cog(is_open=False)

    view = pari_xp.RouletteXPView(cog)
    text = _view_text(view)

    assert "👑 Casino du Refuge" in text
    assert "Maison Royale · Roulette XP" in text
    assert "🔒 Portes fermées" in text
    assert "Réouverture à **10:00**" in text
    assert "%" not in text
    assert not any(isinstance(item, discord.ui.Button) for item in view.walk_children())


@pytest.mark.asyncio
async def test_check_schedule_updates_active_cog_state():
    cog = _make_cog(is_open=False)
    cog._is_open_now = lambda: True
    cog._save_state = AsyncMock()
    cog._announce_state = AsyncMock()
    cog._ensure_roulette_message = AsyncMock()

    await pari_xp.PariXPCog.check_schedule.coro(cog)

    assert cog.is_open is True
    assert cog.state["is_open"] is True
    cog._save_state.assert_awaited_once()
    cog._announce_state.assert_awaited_once()
    cog._ensure_roulette_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_state_uses_deep_snapshot(monkeypatch):
    cog = _make_cog()
    cog.state["players"] = {"42": {"bets": 1}}
    captured = {}

    async def fake_write(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        cog.state["players"]["42"]["bets"] = 99

    monkeypatch.setattr(pari_xp, "atomic_write_json_async", fake_write)

    await cog._save_state()

    assert captured["path"] == pari_xp.STATE_FILE
    assert captured["payload"] is not cog.state
    assert captured["payload"]["players"] is not cog.state["players"]
    assert captured["payload"]["players"]["42"]["bets"] == 1
    assert cog.state["players"]["42"]["bets"] == 99


@pytest.mark.asyncio
async def test_handle_bet_win_uses_active_atomic_debit(monkeypatch):
    cog = _make_cog(is_open=True)
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    debit = AsyncMock()
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", debit)
    credit = AsyncMock(return_value=(1, 1, 80, 120))
    monkeypatch.setattr(pari_xp, "award_xp", credit)
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

    debit.assert_awaited_once_with(42, amount=-20, guild_id=123, source="pari_xp")
    credit.assert_awaited_once_with(42, 40, guild_id=123, source="pari_xp")
    assert cog.state["total_bets"] == 20
    assert cog.state["total_winnings"] == 40
    assert cog.state["last_winner"]["user_id"] == 42
    cog._save_state.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
    result_message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_bet_loss_starts_at_new_simple_cutoff(monkeypatch):
    cog = _make_cog(is_open=True)
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", AsyncMock())
    credit = AsyncMock()
    monkeypatch.setattr(pari_xp, "award_xp", credit)
    cutoff = pari_xp.HOUSE_ZERO_CHANCE + pari_xp.SIMPLE_BET_WIN_CHANCE
    monkeypatch.setattr(pari_xp.random, "random", lambda: cutoff)
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

    credit.assert_not_awaited()
    assert cog.state["total_bets"] == 20
    assert cog.state["total_winnings"] == 0
    final_embed = result_message.edit.await_args.kwargs["embed"]
    assert final_embed.description == "❌ Perdu."


@pytest.mark.asyncio
async def test_handle_bet_refunds_stake_when_payout_fails(monkeypatch):
    cog = _make_cog(is_open=True)
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    debit = AsyncMock()
    refund = AsyncMock()
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", debit)
    monkeypatch.setattr(pari_xp.xp_adapter, "refund_xp_exact", refund)
    monkeypatch.setattr(
        pari_xp,
        "award_xp",
        AsyncMock(side_effect=RuntimeError("credit failed")),
    )
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.10)
    respond = AsyncMock()
    monkeypatch.setattr(pari_xp, "safe_respond", respond)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
    )

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "red", 20)

    debit.assert_awaited_once_with(42, amount=-20, guild_id=123, source="pari_xp")
    refund.assert_awaited_once_with(
        42,
        20,
        guild_id=123,
        source="pari_xp_refund",
    )
    respond.assert_awaited_once_with(
        interaction,
        "❌ Erreur interne pendant le paiement. Ta mise a été remboursée.",
        ephemeral=True,
    )
    assert cog.state["total_bets"] == 0
    assert cog.state["total_winnings"] == 0
    cog._save_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_bet_green_zero_does_not_credit(monkeypatch):
    cog = _make_cog(is_open=True)
    cog._save_state = AsyncMock()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", AsyncMock())
    credit = AsyncMock()
    monkeypatch.setattr(pari_xp, "award_xp", credit)
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.01)
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

    credit.assert_not_awaited()
    assert cog.state["total_bets"] == 20
    assert cog.state["total_winnings"] == 0
    final_embed = result_message.edit.await_args.kwargs["embed"]
    assert "Zéro Vert" in (final_embed.description or "")
    assert "Maison" in (final_embed.description or "")


@pytest.mark.asyncio
async def test_handle_bet_reports_unexpected_debit_error(monkeypatch):
    cog = _make_cog(is_open=True)

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(
        pari_xp.xp_adapter,
        "add_xp",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    respond = AsyncMock()
    monkeypatch.setattr(pari_xp, "safe_respond", respond)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
    )

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "red", 20)

    respond.assert_awaited_once_with(interaction, "❌ Erreur interne.", ephemeral=True)
    assert cog.state["total_bets"] == 0
