import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.pari_xp as pari_xp


def _make_cog():
    cog = object.__new__(pari_xp.PariXPCog)
    cog.bot = object()
    cog.tz = pari_xp.PARIS_TZ
    cog.state = {"is_open": True, "total_bets": 0, "total_winnings": 0}
    cog.is_open = True
    cog._message_id = None
    cog._last_announced_state = None
    cog._last_panel_signature = None
    cog._bet_lock = asyncio.Lock()
    cog._save_state = AsyncMock()
    return cog


def _interaction(user_id: int):
    result_message = SimpleNamespace(edit=AsyncMock())
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=123,
        guild=None,
        response=SimpleNamespace(send_message=AsyncMock()),
        original_response=AsyncMock(return_value=result_message),
    )


@pytest.mark.asyncio
async def test_concurrent_bets_are_serialized_before_debit(monkeypatch):
    cog = _make_cog()
    first_debit_started = asyncio.Event()
    release_first_debit = asyncio.Event()
    second_debit_started = asyncio.Event()
    debit_order: list[int] = []

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )

    async def debit(user_id, *, amount, guild_id, source):
        debit_order.append(user_id)
        if user_id == 1:
            first_debit_started.set()
            await release_first_debit.wait()
        else:
            second_debit_started.set()

    monkeypatch.setattr(pari_xp.xp_adapter, "add_xp", debit)
    monkeypatch.setattr(pari_xp, "award_xp", AsyncMock())
    monkeypatch.setattr(pari_xp.random, "random", lambda: 0.90)
    monkeypatch.setattr(pari_xp.asyncio, "sleep", AsyncMock())

    first = asyncio.create_task(
        pari_xp.PariXPCog._handle_bet(cog, _interaction(1), "red", 20)
    )
    await first_debit_started.wait()

    second = asyncio.create_task(
        pari_xp.PariXPCog._handle_bet(cog, _interaction(2), "red", 20)
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(second_debit_started.wait(), timeout=0.01)

    release_first_debit.set()
    await asyncio.gather(first, second)

    assert second_debit_started.is_set()
    assert debit_order == [1, 2]
    assert cog.state["total_bets"] == 40
