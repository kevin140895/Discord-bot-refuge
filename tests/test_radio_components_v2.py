from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.radio import RADIO_CUSTOM_IDS, RadioCog, _is_radio_message
from ui.radio_view import RadioView


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


def test_radio_panel_uses_components_v2_and_preserves_custom_ids() -> None:
    view = RadioView()

    assert isinstance(view, discord.ui.LayoutView)
    assert "## 📻 RADIO DU REFUGE" in _text(view)
    assert "### 🎚️ Stations disponibles" in _text(view)
    assert {button.custom_id for button in _buttons(view)} == RADIO_CUSTOM_IDS


def test_radio_message_detection_handles_nested_v2_sections() -> None:
    view = RadioView()
    message = SimpleNamespace(components=view.children)

    assert _is_radio_message(message)


@pytest.mark.asyncio
async def test_stored_legacy_radio_message_is_upgraded_in_place() -> None:
    legacy_row = SimpleNamespace(
        children=[SimpleNamespace(custom_id=custom_id) for custom_id in RADIO_CUSTOM_IDS]
    )
    message = SimpleNamespace(
        id=321,
        components=[legacy_row],
        edit=AsyncMock(),
    )

    class DummyChannel:
        id = 123

        async def fetch_message(self, message_id: int):
            assert message_id == message.id
            return message

        send = AsyncMock()

    channel = DummyChannel()
    store = SimpleNamespace(
        get_radio_message=Mock(
            return_value={"channel_id": channel.id, "message_id": message.id}
        ),
        set_radio_message=Mock(),
    )
    cog = object.__new__(RadioCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog.store = store

    await RadioCog._ensure_radio_message(cog, channel)

    message.edit.assert_awaited_once()
    kwargs = message.edit.await_args.kwargs
    assert kwargs["content"] is None
    assert kwargs["embeds"] == []
    assert kwargs["attachments"] == []
    assert isinstance(kwargs["view"], discord.ui.LayoutView)
    channel.send.assert_not_awaited()


def test_radio_message_detection_requires_the_complete_control_contract() -> None:
    incomplete = SimpleNamespace(
        components=[
            SimpleNamespace(
                children=[SimpleNamespace(custom_id="radio_hiphop")]
            )
        ]
    )

    assert not _is_radio_message(incomplete)
