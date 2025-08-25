from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from view import RadioView


@pytest.mark.asyncio
async def test_radio_view_buttons_call_commands():
    view = RadioView()
    mapping = {
        "radio_rapfr": "radio_rapfr",
        "radio_rap": "radio_rap",
        "radio_rock": "radio_rock",
        "radio_hiphop": "radio_hiphop",
    }
    for custom_id, cmd_name in mapping.items():
        button = next(
            child for child in view.children if getattr(child, "custom_id", None) == custom_id
        )
        mock = AsyncMock()
        cog = SimpleNamespace(**{cmd_name: SimpleNamespace(callback=mock)})
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=lambda name, c=cog: c)
        )
        await button.callback(interaction)
        mock.assert_awaited_once()
