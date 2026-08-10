from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.feedback_portal as feedback


@pytest.mark.asyncio
async def test_ensure_portal_creates_v2_message_when_missing(monkeypatch) -> None:
    class DummyChannel:
        def __init__(self) -> None:
            self.send = AsyncMock(return_value=SimpleNamespace(id=123))

        def history(self, *, limit: int):
            assert limit == 50

            async def iterator():
                if False:
                    yield None

            return iterator()

    channel = DummyChannel()
    monkeypatch.setattr(feedback.discord.abc, "Messageable", DummyChannel)

    bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_channel=lambda _channel_id: channel,
    )
    cog = feedback.FeedbackPortalCog(bot)

    await cog.ensure_portal_message()

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert set(kwargs) == {"view"}
    assert isinstance(kwargs["view"], discord.ui.LayoutView)
