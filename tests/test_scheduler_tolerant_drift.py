import importlib
import sys
import asyncio
from pathlib import Path


def test_scheduler_opens_when_minute_not_zero():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.config = {"open_hour": 8, "close_hour": 2, "last_call_hour": 1, "last_call_minute": 45}
    cog.state = {"is_open": False}

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
