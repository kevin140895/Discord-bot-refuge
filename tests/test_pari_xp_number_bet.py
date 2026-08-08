from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.pari_xp as pari_xp


def _make_cog():
    cog = object.__new__(pari_xp.PariXPCog)
    cog.bot = object()
    cog.tz = pari_xp.PARIS_TZ
    cog.state = {
        "is_open": True,
        "total_bets": 0,
        "total_winnings": 0,
        "players": {},
    }
    cog.is_open = True
    cog._message_id = None
    cog._last_announced_state = None
    cog._save_state = AsyncMock()
    return cog


def _make_interaction():
    result_message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
        response=SimpleNamespace(send_message=AsyncMock()),
        original_response=AsyncMock(return_value=result_message),
    )
    return interaction, result_message


def test_number_draw_preserves_zero_and_selected_number_bands():
    assert pari_xp._draw_number_for_roll(17, 0.00) == 0
    assert pari_xp._draw_number_for_roll(17, 0.029999) == 0
    assert pari_xp._draw_number_for_roll(17, 0.03) == 17
    assert pari_xp._draw_number_for_roll(17, 0.079999) == 17

    first_loss = pari_xp._draw_number_for_roll(17, 0.08)
    last_loss = pari_xp._draw_number_for_roll(17, 0.999999)
    assert 1 <= first_loss <= 36
    assert 1 <= last_loss <= 36
    assert first_loss != 17
    assert last_loss != 17


@pytest.mark.parametrize("selected", [0, 37])
def test_number_draw_rejects_values_reserved_or_outside_range(selected):
    with pytest.raises(ValueError, match="between 1 and 36"):
        pari_xp._draw_number_for_roll(selected, 0.04)


@pytest.mark.asyncio
async def test_number_bet_wins_only_when_draw_matches_selected_number(monkeypatch):
    cog = _make_cog()
    interaction, result_message = _make_interaction()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 1000, "level": 3}),
    )
    debit = AsyncMock()
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", debit)
    credit = AsyncMock()
    monkeypatch.setattr(pari_xp, "award_xp", credit)
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.04)
    monkeypatch.setattr(pari_xp.asyncio, "sleep", AsyncMock())

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "number", 20, 17)

    debit.assert_awaited_once_with(42, amount=-20, guild_id=123, source="pari_xp")
    credit.assert_awaited_once_with(42, 200, guild_id=123, source="pari_xp")
    final_embed = result_message.edit.await_args.kwargs["embed"]
    description = final_embed.description or ""
    assert "Numéro tiré : 17" in description
    assert "ton choix : 17" in description
    assert "Gagné" in description


@pytest.mark.asyncio
async def test_number_bet_loss_displays_a_different_drawn_number(monkeypatch):
    cog = _make_cog()
    interaction, result_message = _make_interaction()

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 1000, "level": 3}),
    )
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", AsyncMock())
    credit = AsyncMock()
    monkeypatch.setattr(pari_xp, "award_xp", credit)
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.50)
    monkeypatch.setattr(pari_xp.asyncio, "sleep", AsyncMock())

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "number", 20, 17)

    credit.assert_not_awaited()
    final_embed = result_message.edit.await_args.kwargs["embed"]
    description = final_embed.description or ""
    assert "ton choix : 17" in description
    assert "Numéro tiré : 17" not in description
    assert "Perdu" in description


@pytest.mark.asyncio
async def test_number_zero_is_rejected_before_balance_check_or_debit(monkeypatch):
    cog = _make_cog()
    interaction, _ = _make_interaction()

    get_user_data = AsyncMock(return_value={"xp": 1000, "level": 3})
    monkeypatch.setattr(pari_xp.xp_store, "get_user_data", get_user_data)
    debit = AsyncMock()
    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", debit)
    respond = AsyncMock()
    monkeypatch.setattr(pari_xp, "safe_respond", respond)

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "number", 20, 0)

    respond.assert_awaited_once_with(
        interaction,
        "❌ Numéro invalide (1-36).",
        ephemeral=True,
    )
    get_user_data.assert_not_awaited()
    debit.assert_not_awaited()
