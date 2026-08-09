from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog, ShopView


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def _cog() -> EconomyUICog:
    cog = object.__new__(EconomyUICog)
    cog.bot = SimpleNamespace()
    return cog


def test_shop_uses_components_v2_without_changing_purchase_custom_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {
            "ticket_royal": {"name": "Ticket Royal", "price": 500},
            "double_xp_1h": {"name": "Double XP 1h", "price": 300},
        },
    )

    view = ShopView(_cog())
    text = _text(view)
    buttons = _buttons(view)

    assert isinstance(view, discord.ui.LayoutView)
    assert "## 🛒 BOUTIQUE DU REFUGE" in text
    assert "### 🎟️ TICKET ROYAL" in text
    assert "### ⚡ DOUBLE XP 1H" in text
    assert "500 XP" in text
    assert "300 XP" in text
    assert "Stock maximum : 3" in text
    assert "Maximum actif : 2" in text
    assert [button.custom_id for button in buttons] == [
        "shop_buy:ticket_royal",
        "shop_buy:double_xp_1h",
    ]
    assert [button.label for button in buttons] == [
        "Acheter · 500 XP",
        "Activer · 300 XP",
    ]


def test_shop_v2_still_excludes_vip_items(monkeypatch) -> None:
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {
            "vip_24h": {"name": "VIP 24h", "price": 100},
            "ticket_royal": {"name": "Ticket Royal", "price": 500},
        },
    )

    view = ShopView(_cog())

    assert "vip" not in _text(view).lower()
    assert [button.custom_id for button in _buttons(view)] == [
        "shop_buy:ticket_royal"
    ]


@pytest.mark.asyncio
async def test_existing_shop_message_is_migrated_in_place_to_layout_view(monkeypatch) -> None:
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {
            "ticket_royal": {"name": "Ticket Royal", "price": 500},
            "double_xp_1h": {"name": "Double XP 1h", "price": 300},
        },
    )
    view = ShopView(_cog())
    message = SimpleNamespace(id=42, edit=AsyncMock())

    class DummyChannel:
        async def fetch_message(self, message_id: int):
            assert message_id == 42
            return message

        send = AsyncMock()

    channel = DummyChannel()
    cog = _cog()

    message_id = await EconomyUICog._ensure_message(
        cog,
        channel,
        42,
        None,
        view,
        "Boutique",
    )

    assert message_id == 42
    message.edit.assert_awaited_once_with(
        content=None,
        embeds=[],
        attachments=[],
        view=view,
    )
    channel.send.assert_not_awaited()
