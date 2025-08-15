from datetime import datetime
from zoneinfo import ZoneInfo

from utils.timewin import is_open_now, next_boundary_dt

TZ = "Europe/Paris"


def test_is_open_now_handles_midnight_wrap():
    # Window crossing midnight: 22h -> 06h
    window_start = 22
    window_end = 6
    tzinfo = ZoneInfo(TZ)

    # At start boundary
    now = datetime(2024, 1, 1, 22, 0, tzinfo=tzinfo)
    assert is_open_now(TZ, window_start, window_end, now=now)

    # During window before midnight
    now = datetime(2024, 1, 1, 23, 0, tzinfo=tzinfo)
    assert is_open_now(TZ, window_start, window_end, now=now)

    # During window after midnight
    now = datetime(2024, 1, 2, 5, 59, tzinfo=tzinfo)
    assert is_open_now(TZ, window_start, window_end, now=now)

    # At end boundary
    now = datetime(2024, 1, 2, 6, 0, tzinfo=tzinfo)
    assert not is_open_now(TZ, window_start, window_end, now=now)

    # Before window starts
    now = datetime(2024, 1, 1, 21, 0, tzinfo=tzinfo)
    assert not is_open_now(TZ, window_start, window_end, now=now)


def test_next_boundary_dt_midnight_wrap():
    tzinfo = ZoneInfo(TZ)
    now = datetime(2024, 1, 1, 23, 0, tzinfo=tzinfo)
    nxt = next_boundary_dt(now=now, tz=TZ, start_h=22, end_h=6)
    assert nxt == datetime(2024, 1, 2, 6, 0, tzinfo=tzinfo)

