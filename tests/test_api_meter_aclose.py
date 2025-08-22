import os
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.api_meter import APIMeter


@pytest.mark.asyncio
async def test_aclose_cancels_and_resets_tasks():
    meter = APIMeter()
    writer = asyncio.create_task(asyncio.sleep(10))
    summary = asyncio.create_task(asyncio.sleep(10))
    meter.writer_task = writer
    meter.summary_task = summary

    await asyncio.sleep(0)
    await meter.aclose()

    assert writer.done()
    assert summary.done()
    assert meter.writer_task is None
    assert meter.summary_task is None
