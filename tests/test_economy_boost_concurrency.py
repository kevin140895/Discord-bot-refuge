import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog


def _interaction(user_id: int = 1):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=123,
        response=SimpleNamespace(send_message=AsyncMock()),
    )


def _double_xp_shop():
    return {"double_xp_1h": {"name": "Double XP 1h", "price": 300}}


@pytest.mark.asyncio
async def test_concurrent_double_xp_purchases_respect_active_limit(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "1": [
            {
                "type": "double_xp",
                "until": (now + timedelta(hours=1)).isoformat(),
            }
        ]
    }

    monkeypatch.setattr(economy_ui, "_load_shop", _double_xp_shop)
    monkeypatch.setattr(economy_ui, "load_boosts", lambda: deepcopy(state))

    async def save_boosts(data):
        state.clear()
        state.update(deepcopy(data))

    monkeypatch.setattr(economy_ui, "save_boosts", save_boosts)
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    monkeypatch.setattr(
        economy_ui,
        "_activate_personal_double_xp",
        lambda _uid, _minutes: now + timedelta(hours=2),
    )

    first_debit_started = asyncio.Event()
    release_first_debit = asyncio.Event()
    debit_calls = []

    async def blocked_debit(user_id, *, amount, guild_id, source):
        debit_calls.append((user_id, amount, guild_id, source))
        first_debit_started.set()
        await release_first_debit.wait()

    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", blocked_debit)
    transaction_add = AsyncMock()
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(add=transaction_add),
    )

    cog = EconomyUICog(object())
    first = _interaction()
    second = _interaction()

    first_task = asyncio.create_task(
        cog._handle_shop_purchase(first, "double_xp_1h")
    )
    await first_debit_started.wait()

    second_task = asyncio.create_task(
        cog._handle_shop_purchase(second, "double_xp_1h")
    )
    await asyncio.sleep(0)

    # The second purchase must still be waiting on the shared boost lock. If it
    # reaches the debit concurrently, both purchases could pass the active=1
    # check and create a third active boost.
    assert len(debit_calls) == 1

    release_first_debit.set()
    await asyncio.gather(first_task, second_task)

    assert len(debit_calls) == 1
    assert len(state["1"]) == 2
    transaction_add.assert_awaited_once()

    responses = [
        first.response.send_message.await_args.args[0],
        second.response.send_message.await_args.args[0],
    ]
    assert sum("effectu" in response.lower() for response in responses) == 1
    assert sum("limite" in response.lower() for response in responses) == 1


@pytest.mark.asyncio
async def test_boost_cleanup_serializes_disk_write_but_not_discord_role_removal(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    state = {
        "1": [
            {
                "type": "role_boost",
                "role_id": 777,
                "until": (now - timedelta(minutes=1)).isoformat(),
            }
        ]
    }

    monkeypatch.setattr(economy_ui, "load_boosts", lambda: deepcopy(state))

    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def blocked_save(data):
        save_started.set()
        await release_save.wait()
        state.clear()
        state.update(deepcopy(data))

    monkeypatch.setattr(economy_ui, "save_boosts", blocked_save)

    role_removal_started = asyncio.Event()
    release_role_removal = asyncio.Event()

    async def blocked_remove_role(*_args, **_kwargs):
        role_removal_started.set()
        await release_role_removal.wait()

    member = SimpleNamespace(remove_roles=AsyncMock(side_effect=blocked_remove_role))
    role = object()
    guild = SimpleNamespace(
        get_member=lambda _uid: member,
        get_role=lambda _rid: role,
    )
    bot = SimpleNamespace(get_guild=lambda _gid: guild)
    cog = EconomyUICog(bot)

    cleanup_task = asyncio.create_task(cog._cleanup_boosts_once())
    await save_started.wait()

    monkeypatch.setattr(economy_ui, "_load_shop", _double_xp_shop)
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    monkeypatch.setattr(
        economy_ui,
        "_activate_personal_double_xp",
        lambda _uid, _minutes: now + timedelta(hours=1),
    )

    debit_started = asyncio.Event()

    async def debit(user_id, *, amount, guild_id, source):
        debit_started.set()

    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", debit)
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(add=AsyncMock()),
    )

    purchase = _interaction()
    purchase_task = asyncio.create_task(
        cog._handle_shop_purchase(purchase, "double_xp_1h")
    )
    await asyncio.sleep(0)

    # Cleanup still owns the boost lock while its persisted state is blocked,
    # so the purchase cannot debit XP yet.
    assert not debit_started.is_set()

    release_save.set()
    await role_removal_started.wait()

    # The role removal is a Discord network operation. It must happen after the
    # boost lock is released, allowing the purchase to proceed immediately.
    await asyncio.wait_for(debit_started.wait(), timeout=1)

    release_role_removal.set()
    await asyncio.gather(cleanup_task, purchase_task)

    assert len(state["1"]) == 1
    assert state["1"][0]["type"] == "double_xp"
    member.remove_roles.assert_awaited_once_with(role, reason="Boost expiré")
