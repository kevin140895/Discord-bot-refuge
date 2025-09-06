import importlib
import sys
import asyncio
from pathlib import Path


def _setup_cog(pari_xp_module):
    """Create a cog instance with basic config and state."""
    cog = object.__new__(pari_xp_module.RouletteRefugeCog)
    cog.config = {"open_hour": 8, "close_hour": 2, "last_call_hour": 1, "last_call_minute": 45}
    cog.state = {"is_open": False}
    return cog


def _stub_dependencies(cog):
    async def fake_get_channel():
        class Dummy:
            id = 0

        return Dummy()

    async def announce_open(_):
        cog.state["opened"] = True

    async def update_hub_state(is_open: bool) -> None:
        cog.state["is_open"] = is_open

    cog._get_channel = fake_get_channel  # type: ignore[attr-defined]
    cog._announce_open = announce_open  # type: ignore[attr-defined]
    cog._update_hub_state = update_hub_state  # type: ignore[attr-defined]


def test_scheduler_opens_when_minute_not_zero():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = _setup_cog(pari_xp)
    _stub_dependencies(cog)

    orig_dt = pari_xp.datetime

    class FakeDateTime(orig_dt):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return orig_dt(2023, 1, 1, 8, 1, tzinfo=tz)

    pari_xp.datetime = FakeDateTime
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(pari_xp.RouletteRefugeCog.scheduler_task.coro(cog))
    finally:
        pari_xp.datetime = orig_dt
        asyncio.set_event_loop(None)
        loop.close()

    assert cog.state.get("opened") and cog.state.get("is_open")


def test_scheduler_opens_if_started_late():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = _setup_cog(pari_xp)
    _stub_dependencies(cog)

    orig_dt = pari_xp.datetime

    class FakeDateTime(orig_dt):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return orig_dt(2023, 1, 1, 10, 0, tzinfo=tz)

    pari_xp.datetime = FakeDateTime
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(pari_xp.RouletteRefugeCog.scheduler_task.coro(cog))
    finally:
        pari_xp.datetime = orig_dt
        asyncio.set_event_loop(None)
        loop.close()

    assert cog.state.get("opened") and cog.state.get("is_open")


def test_scheduler_reannounces_if_last_open_stale():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = _setup_cog(pari_xp)
    cog.state = {
        "is_open": True,
        "hub_message_id": None,
        "last_open_announce_ts": "2023-01-01T08:00:00",
    }
    _stub_dependencies(cog)

    announced = False

    async def announce_open(_):
        nonlocal announced
        announced = True

    cog._announce_open = announce_open  # type: ignore[attr-defined]

    orig_dt = pari_xp.datetime

    class FakeDateTime(orig_dt):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return orig_dt(2023, 1, 2, 8, 5, tzinfo=tz)

    pari_xp.datetime = FakeDateTime
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(pari_xp.RouletteRefugeCog.scheduler_task.coro(cog))
    finally:
        pari_xp.datetime = orig_dt
        asyncio.set_event_loop(None)
        loop.close()

    assert announced and cog.state.get("is_open")


def test_scheduler_waits_until_top_of_minute():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = _setup_cog(pari_xp)
    _stub_dependencies(cog)

    class DummyBot:
        async def wait_until_ready(self):
            return

    cog.bot = DummyBot()

    orig_dt = pari_xp.datetime
    tz = pari_xp.ZoneInfo("Europe/Paris")

    class FakeDateTime(orig_dt):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return orig_dt(2023, 1, 1, 8, 0, 30, tzinfo=tz)

    called_target = None

    async def fake_sleep_until(target):
        nonlocal called_target
        called_target = target

    pari_xp.datetime = FakeDateTime
    orig_sleep_until = pari_xp.discord.utils.sleep_until
    pari_xp.discord.utils.sleep_until = fake_sleep_until

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(pari_xp.RouletteRefugeCog._wait_ready_scheduler(cog))
    finally:
        pari_xp.datetime = orig_dt
        pari_xp.discord.utils.sleep_until = orig_sleep_until
        asyncio.set_event_loop(None)
        loop.close()

    assert called_target == orig_dt(2023, 1, 1, 8, 1, tzinfo=tz)

