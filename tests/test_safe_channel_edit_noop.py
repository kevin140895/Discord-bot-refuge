import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from utils.discord_utils import safe_channel_edit


@pytest.mark.asyncio
async def test_safe_channel_edit_noop():
    channel = SimpleNamespace(id=1, name="Foo", edit=AsyncMock())
    await safe_channel_edit(channel, name="Foo")
    channel.edit.assert_not_awaited()
