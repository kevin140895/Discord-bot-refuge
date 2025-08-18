from datetime import datetime
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import utils.timewin as tw


def test_is_open_now_true(monkeypatch):
    class MockDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 1, 1, 12, tzinfo=tz)

    monkeypatch.setattr(tw, "datetime", MockDT)
    assert tw.is_open_now("UTC", 10, 22)


def test_is_open_now_false(monkeypatch):
    class MockDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 1, 1, 23, tzinfo=tz)

    monkeypatch.setattr(tw, "datetime", MockDT)
    assert not tw.is_open_now("UTC", 10, 22)


def test_next_boundary_dt():
    tz = ZoneInfo("UTC")
    now = datetime(2023, 1, 1, 9, tzinfo=tz)
    assert tw.next_boundary_dt(now, tz="UTC", start_h=10, end_h=22) == datetime(
        2023, 1, 1, 10, tzinfo=tz
    )

    late = datetime(2023, 1, 1, 23, tzinfo=tz)
    assert tw.next_boundary_dt(late, tz="UTC", start_h=10, end_h=22) == datetime(
        2023, 1, 2, 10, tzinfo=tz
    )

