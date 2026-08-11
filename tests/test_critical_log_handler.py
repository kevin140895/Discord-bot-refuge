import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


_MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"
_MAIN_SPEC = importlib.util.spec_from_file_location("refuge_entrypoint", _MAIN_PATH)
assert _MAIN_SPEC is not None and _MAIN_SPEC.loader is not None
_MAIN_MODULE = importlib.util.module_from_spec(_MAIN_SPEC)
_MAIN_SPEC.loader.exec_module(_MAIN_MODULE)
DiscordCriticalHandler = _MAIN_MODULE.DiscordCriticalHandler
RailwayJsonFormatter = _MAIN_MODULE.RailwayJsonFormatter
configure_logging = _MAIN_MODULE.configure_logging


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


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.DEBUG, "debug"),
        (logging.INFO, "info"),
        (logging.WARNING, "warn"),
        (logging.ERROR, "error"),
        (logging.CRITICAL, "error"),
    ],
)
def test_railway_json_formatter_maps_levels(level: int, expected: str):
    record = logging.LogRecord(
        name="test.railway",
        level=level,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )

    payload = json.loads(RailwayJsonFormatter().format(record))

    assert payload["level"] == expected
    assert payload["logger"] == "test.railway"
    assert payload["message"] == "hello world"
    assert payload["timestamp"].endswith("Z")


def test_railway_json_formatter_preserves_exception():
    try:
        raise ValueError("bad payload")
    except ValueError:
        record = logging.LogRecord(
            name="test.railway",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(RailwayJsonFormatter().format(record))

    assert payload["level"] == "error"
    assert "ValueError: bad payload" in payload["exception"]


def test_configure_logging_uses_single_stdout_json_handler(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        logging,
        "basicConfig",
        lambda **kwargs: captured.update(kwargs),
    )

    configure_logging()

    assert captured["level"] == logging.INFO
    assert captured["force"] is True
    handlers = captured["handlers"]
    assert isinstance(handlers, list)
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout
    assert isinstance(handler.formatter, RailwayJsonFormatter)


def test_main_disables_discord_default_log_handler(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBot:
        def __init__(self, *args, **kwargs) -> None:
            captured["init_args"] = args
            captured["init_kwargs"] = kwargs

        def run(self, token: str, **kwargs) -> None:
            captured["token"] = token
            captured["run_kwargs"] = kwargs

    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CRITICAL_LOG_CHANNEL_ID", raising=False)
    monkeypatch.setattr(_MAIN_MODULE, "configure_logging", lambda: None)
    monkeypatch.setattr(_MAIN_MODULE, "RefugeBot", FakeBot)

    _MAIN_MODULE.main()

    assert captured["token"] == "test-token"
    assert captured["run_kwargs"] == {"log_handler": None}


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
