import importlib
from pathlib import Path
import sys
import asyncio


def test_is_open_hours_respects_config_and_embed():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.config = {"open_hour": 10, "close_hour": 3}
    cog.state = {}
    tz = pari_xp.timezones.TZ_PARIS

    assert cog._is_open_hours(pari_xp.datetime(2023, 1, 1, 10, 0, tzinfo=tz))
    assert cog._is_open_hours(pari_xp.datetime(2023, 1, 2, 2, 59, tzinfo=tz))
    assert not cog._is_open_hours(pari_xp.datetime(2023, 1, 1, 9, 59, tzinfo=tz))
    assert not cog._is_open_hours(pari_xp.datetime(2023, 1, 2, 3, 0, tzinfo=tz))

    cog._now = lambda: pari_xp.datetime(2023, 1, 1, 11, 0, tzinfo=tz)
    desc = cog._build_hub_embed().description or ""
    assert "ferme à ⏰ 03:00" in desc


def test_hub_view_respects_state_override():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.config = {"open_hour": 10, "close_hour": 3}
    cog.state = {"is_open": True}
    cog._is_open_hours = lambda dt=None: False

    desc = cog._build_hub_embed().description or ""
    assert "🟢 État : Ouvert" in desc
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        async def run():
            return cog._build_hub_view()
        view = loop.run_until_complete(run())
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    assert any(isinstance(c, pari_xp.ui.Button) for c in view.children)
