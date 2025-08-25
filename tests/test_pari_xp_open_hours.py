import importlib
from pathlib import Path
import sys


def test_is_open_hours_respects_config_and_embed():
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.config = {"open_hour": 10, "close_hour": 3}
    tz = pari_xp.timezones.TZ_PARIS

    assert cog._is_open_hours(pari_xp.datetime(2023, 1, 1, 10, 0, tzinfo=tz))
    assert cog._is_open_hours(pari_xp.datetime(2023, 1, 2, 2, 59, tzinfo=tz))
    assert not cog._is_open_hours(pari_xp.datetime(2023, 1, 1, 9, 59, tzinfo=tz))
    assert not cog._is_open_hours(pari_xp.datetime(2023, 1, 2, 3, 0, tzinfo=tz))

    cog._now = lambda: pari_xp.datetime(2023, 1, 1, 11, 0, tzinfo=tz)
    desc = cog._build_hub_embed().description or ""
    assert "ferme à ⏰ 03:00" in desc
