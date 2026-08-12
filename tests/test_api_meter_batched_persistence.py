import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.api_meter import APICallCtx, APIMeter


PARIS = ZoneInfo("Europe/Paris")


def _ctx(*, started_at: float | None = None) -> APICallCtx:
    return APICallCtx(
        lib="discord.py",
        method="GET",
        route="/channels/{id}/messages",
        major_param="channels:123",
        status=200,
        duration_ms=25,
        retry_after_ms=0,
        bucket=None,
        ratelimit_remaining=None,
        ratelimit_reset=None,
        error_code=None,
        started_at=started_at,
    )


def _event(ts: str, *, route: str = "/channels/{id}/messages") -> dict:
    return {
        "lib": "discord.py",
        "method": "GET",
        "route": route,
        "major_param": "channels:123",
        "status": 200,
        "duration_ms": 25,
        "retry_after_ms": 0,
        "bucket": None,
        "ratelimit_remaining": None,
        "ratelimit_reset": None,
        "error_code": None,
        "cog": None,
        "command": None,
        "caller": None,
        "size_bytes": None,
        "ts": ts,
    }


def test_record_call_only_queues_and_uses_explicit_context(tmp_path):
    meter = APIMeter()
    meter.data_dir = tmp_path
    started = datetime(2026, 8, 31, 23, 59, 59, tzinfo=PARIS)

    token = meter.set_context("RadioCog", "play")
    try:
        meter.record_call(_ctx(started_at=started.timestamp()))
    finally:
        meter.reset_context(token)

    # The REST hot path only creates/enqueues the event. Aggregation and disk
    # persistence belong to the worker.
    assert meter.events == meter.events.__class__()
    assert not meter.route_totals
    assert not meter.source_totals
    assert list(tmp_path.iterdir()) == []

    queued = meter.queue.get_nowait()
    assert queued is not None
    assert queued["cog"] == "RadioCog"
    assert queued["command"] == "play"
    assert queued["caller"] is None
    assert queued["ts"] == started.isoformat()


@pytest.mark.asyncio
async def test_month_buckets_are_stable_for_late_previous_month_events(tmp_path):
    meter = APIMeter()
    meter.data_dir = tmp_path

    await meter._ingest_event(_event("2026-08-31T23:59:59+02:00"))
    await meter._ingest_event(_event("2026-09-01T00:00:01+02:00"))
    # Simulate a request started before midnight but completed/queued later.
    await meter._ingest_event(_event("2026-08-31T23:59:58+02:00"))
    await meter._save_aggregates_async()

    august = json.loads((tmp_path / "api_metrics-2026-08.json").read_text())
    september = json.loads((tmp_path / "api_metrics-2026-09.json").read_text())

    route = "GET /channels/{id}/messages"
    assert august["routes"][route]["calls"] == 2
    assert september["routes"][route]["calls"] == 1
    assert meter.current_month == "2026-09"


def test_raw_jsonl_uses_event_day_not_flush_day(tmp_path):
    meter = APIMeter()
    meter.data_dir = tmp_path

    meter._flush(
        [
            _event("2026-08-31T23:59:59+02:00"),
            _event("2026-09-01T00:00:01+02:00"),
        ]
    )

    august_path = tmp_path / "api_metrics-2026-08-31.jsonl"
    september_path = tmp_path / "api_metrics-2026-09-01.jsonl"
    assert august_path.exists()
    assert september_path.exists()
    assert len(august_path.read_text().splitlines()) == 1
    assert len(september_path.read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_shutdown_flushes_pending_ram_metrics(tmp_path, monkeypatch):
    meter = APIMeter()
    meter.data_dir = tmp_path
    started = datetime(2026, 8, 12, 22, 0, 0, tzinfo=PARIS)

    # Keep the periodic write far away: shutdown itself must persist the data.
    monkeypatch.setattr(
        "config.API_METER_PERSIST_INTERVAL_SECONDS",
        60,
        raising=False,
    )
    meter._load_aggregates(started)
    await meter.start(object())

    meter.record_call(_ctx(started_at=started.timestamp()))
    await meter.aclose()

    raw_path = tmp_path / "api_metrics-2026-08-12.jsonl"
    monthly_path = tmp_path / "api_metrics-2026-08.json"
    assert raw_path.exists()
    assert monthly_path.exists()

    monthly = json.loads(monthly_path.read_text())
    assert monthly["routes"]["GET /channels/{id}/messages"]["calls"] == 1
