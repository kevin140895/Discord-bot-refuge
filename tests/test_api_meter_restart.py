import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.api_meter import APIMeter


def _disable_disk_persistence(meter: APIMeter) -> None:
    meter._load_aggregates = lambda *args, **kwargs: None
    meter._save_aggregates_async = AsyncMock()


@pytest.mark.asyncio
async def test_start_replaces_finished_background_tasks():
    meter = APIMeter()
    _disable_disk_persistence(meter)

    await meter.start(object())
    first_writer = meter.writer_task
    first_summary = meter.summary_task
    assert first_writer is not None
    assert first_summary is not None

    first_writer.cancel()
    first_summary.cancel()
    await asyncio.gather(first_writer, first_summary, return_exceptions=True)
    assert first_writer.done()
    assert first_summary.done()

    await meter.start(object())

    assert meter.writer_task is not None
    assert meter.summary_task is not None
    assert meter.writer_task is not first_writer
    assert meter.summary_task is not first_summary
    assert not meter.writer_task.done()
    assert not meter.summary_task.done()

    await meter.aclose()


@pytest.mark.asyncio
async def test_start_keeps_existing_live_background_tasks():
    meter = APIMeter()
    _disable_disk_persistence(meter)

    await meter.start(object())
    first_writer = meter.writer_task
    first_summary = meter.summary_task

    await meter.start(object())

    assert meter.writer_task is first_writer
    assert meter.summary_task is first_summary

    await meter.aclose()


@pytest.mark.asyncio
async def test_aclose_does_not_poison_queue_when_writer_already_finished():
    meter = APIMeter()
    _disable_disk_persistence(meter)

    finished_writer = asyncio.create_task(asyncio.sleep(0))
    await finished_writer
    meter.writer_task = finished_writer

    await meter.aclose()

    assert meter.writer_task is None
    assert meter.queue.empty()

    await meter.start(object())
    await asyncio.sleep(0)

    assert meter.writer_task is not None
    assert not meter.writer_task.done()

    await meter.aclose()
