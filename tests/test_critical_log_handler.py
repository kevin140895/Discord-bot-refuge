import asyncio
import logging
from types import SimpleNamespace

import pytest

from main import DiscordCriticalHandler


def _record(message: str = "boom") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.critical",
        level=logging.CRITICAL,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_emit_without_running_bot_loop_is_safe():
    bot = SimpleNamespace(loop=None, get_channel=lambda _channel_id: None)
    handler = DiscordCriticalHandler(bot, 123)

    handler.emit(_record())


@pytest.mark.asyncio
async def test_emit_from_worker_thread_schedules_on_bot_loop():
    loop = asyncio.get_running_loop()
    sent = asyncio.Event()
    messages: list[str] = []

    class Channel:
        async def send(self, message: str) -> None:
            messages.append(message)
            sent.set()

    channel = Channel()
    bot = SimpleNamespace(
        loop=loop,
        get_channel=lambda channel_id: channel if channel_id == 123 else None,
    )
    handler = DiscordCriticalHandler(bot, 123)
    handler.setFormatter(logging.Formatter("%(message)s"))

    await asyncio.to_thread(handler.emit, _record("critical from thread"))
    await asyncio.wait_for(sent.wait(), timeout=1)

    assert messages == ["```critical from thread```"]


@pytest.mark.asyncio
async def test_send_failure_is_consumed_without_loop_error():
    loop = asyncio.get_running_loop()
    attempted = asyncio.Event()
    loop_errors: list[dict] = []
    previous_handler = loop.get_exception_handler()

    class FailingChannel:
        async def send(self, _message: str) -> None:
            attempted.set()
            raise RuntimeError("discord unavailable")

    channel = FailingChannel()
    bot = SimpleNamespace(loop=loop, get_channel=lambda _channel_id: channel)
    handler = DiscordCriticalHandler(bot, 123)
    handler.setFormatter(logging.Formatter("%(message)s"))

    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    try:
        await asyncio.to_thread(handler.emit, _record("delivery failure"))
        await asyncio.wait_for(attempted.wait(), timeout=1)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert loop_errors == []
