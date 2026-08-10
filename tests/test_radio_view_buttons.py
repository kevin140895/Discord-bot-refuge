from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from ui.radio_view import RadioView


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        child
        for child in view.walk_children()
        if isinstance(child, discord.ui.Button)
    ]


@pytest.mark.asyncio
async def test_radio_view_has_expected_buttons():
    view = RadioView()
    expected = {
        "radio_rap_fr": "Rap FR",
        "radio_rap": "Rap US",
        "radio_rock": "Rock",
        "radio_hiphop": "Radio Hip-Hop",
    }

    assert isinstance(view, discord.ui.LayoutView)
    assert view.is_persistent()

    buttons = _buttons(view)
    for custom_id, label in expected.items():
        button = next(
            (child for child in buttons if getattr(child, "custom_id", None) == custom_id),
            None,
        )
        assert button is not None, f"Button with custom_id '{custom_id}' not found"
        assert getattr(button, "label", None) == label, (
            f"Label for '{custom_id}' should be '{label}', got '{getattr(button, 'label', None)}'"
        )


@pytest.mark.asyncio
async def test_radio_view_buttons_call_methods():
    view = RadioView()
    mapping = {
        "radio_rap_fr": "radio_rap_fr",
        "radio_rap": "radio_rap",
        "radio_rock": "radio_rock",
        "radio_hiphop": "radio_hiphop",
    }

    buttons = _buttons(view)
    for custom_id, cmd_name in mapping.items():
        button = next(
            (child for child in buttons if getattr(child, "custom_id", None) == custom_id),
            None,
        )
        assert button is not None, f"Button with custom_id '{custom_id}' not found"

        mock = AsyncMock()
        cog = SimpleNamespace(**{cmd_name: mock})
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=lambda name, c=cog: c)
        )

        await button.callback(interaction)

        mock.assert_awaited_once_with(interaction)
