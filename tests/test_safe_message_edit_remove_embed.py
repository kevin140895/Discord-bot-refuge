import os
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.discord_utils import safe_message_edit


@pytest.mark.asyncio
async def test_safe_message_edit_removes_embed(monkeypatch):
    embed = SimpleNamespace(to_dict=lambda: {"a": 1})
    message = SimpleNamespace(
        content="hello",
        embeds=[embed],
        edit=AsyncMock(),
        channel=SimpleNamespace(id=123),
    )
    limiter = SimpleNamespace(acquire=AsyncMock())
    monkeypatch.setattr("utils.discord_utils.limiter", limiter)

    await safe_message_edit(message, embed=None)

    message.edit.assert_awaited_once_with(embed=None)
    limiter.acquire.assert_awaited_once_with(bucket="channel:123")
