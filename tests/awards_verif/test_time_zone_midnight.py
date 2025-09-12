from datetime import datetime, time, timedelta

import pytest

from cogs.daily_awards import PARIS_TZ


def next_midnight(dt: datetime) -> datetime:
    target = datetime.combine(dt.date(), time(hour=0, tzinfo=PARIS_TZ))
    if dt >= target:
        target += timedelta(days=1)
    return target


def seconds_to_next_midnight(dt: datetime) -> int:
    return int((next_midnight(dt) - dt).total_seconds())


def test_dst_transition_march():
    dt = datetime(2024, 3, 31, 0, 30, tzinfo=PARIS_TZ)
    # Clocks go forward; 23.5h until next midnight
    assert seconds_to_next_midnight(dt) == 23 * 3600 + 30 * 60


@pytest.mark.xfail(reason="DST backward not handled")
def test_dst_transition_october():
    dt = datetime(2024, 10, 27, 0, 30, tzinfo=PARIS_TZ)
    # Clocks go back; 24.5h until next midnight
    assert seconds_to_next_midnight(dt) == 24 * 3600 + 30 * 60
