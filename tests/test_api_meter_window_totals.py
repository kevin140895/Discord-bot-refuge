import os
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.api_meter import APIMeter


def _event(route: str, *, status: int = 200, duration_ms: int = 100) -> dict:
    return {
        "method": "GET",
        "route": route,
        "status": status,
        "duration_ms": duration_ms,
        "caller": "test",
        "cog": None,
        "command": None,
    }


def test_window_totals_include_routes_outside_top_five():
    meter = APIMeter()
    now = time.time()

    # Five dominant routes: 10 calls each.
    for route_index in range(5):
        for _ in range(10):
            meter.events.append((now, _event(f"/route-{route_index}")))

    # Sixth route would be excluded by get_top_routes(..., 5), but its call,
    # error, 429 and duration must still count toward budget/alert thresholds.
    meter.events.append(
        (now, _event("/route-outside-top-five", status=429, duration_ms=1100))
    )

    assert sum(item["calls"] for item in meter.get_top_routes(10, 5)) == 50

    totals = meter.get_window_totals(10)
    assert totals["calls"] == 51
    assert totals["errors"] == 1
    assert totals["429"] == 1
    assert totals["avg_ms"] == 6100 / 51
