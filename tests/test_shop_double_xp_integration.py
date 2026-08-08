import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
import cogs.xp as xp


@pytest.mark.asyncio
async def test_shop_double_xp_activates_real_award_path(monkeypatch):
    xp.XP_BOOSTS.clear()
    monkeypatch.setattr(xp, "save_xp_boosts_to_disk", AsyncMock())

    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {"double_xp_1h": {"name": "Double XP 1h", "price": 300}},
    )
    monkeypatch.setattr(economy_ui, "load_boosts", lambda: {})
    save_boosts = AsyncMock()
    monkeypatch.setattr(economy_ui, "save_boosts", save_boosts)
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", AsyncMock())
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(add=AsyncMock()),
    )

    store_add = AsyncMock(return_value=(0, 0, 0, 20))
    monkeypatch.setattr(xp.xp_store, "add_xp", store_add)

    cog = economy_ui.EconomyUICog(object())
    send = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        response=SimpleNamespace(send_message=send),
    )

    await cog._handle_shop_purchase(interaction, "double_xp_1h")
    await asyncio.sleep(0)

    assert "42" in xp.XP_BOOSTS
    persisted = save_boosts.call_args.args[0]
    assert datetime.fromisoformat(persisted["42"][0]["until"]) == xp.XP_BOOSTS["42"]

    await xp.award_xp(42, 10, guild_id=123, source="integration_test")
    store_add.assert_awaited_once_with(
        42,
        20,
        guild_id=123,
        source="integration_test",
    )


@pytest.mark.asyncio
async def test_shop_double_xp_second_activation_extends_duration(monkeypatch):
    xp.XP_BOOSTS.clear()
    monkeypatch.setattr(xp, "save_xp_boosts_to_disk", AsyncMock())

    before = datetime.now(timezone.utc)
    first_expiry = economy_ui._activate_personal_double_xp(7, 60)
    second_expiry = economy_ui._activate_personal_double_xp(7, 60)
    await asyncio.sleep(0)

    extension = second_expiry - first_expiry
    assert timedelta(minutes=59, seconds=59) < extension < timedelta(hours=1, seconds=1)
    assert second_expiry > before + timedelta(minutes=119)
