import os
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from utils.rename_manager import _RenameManager


@pytest.mark.asyncio
async def test_aclose_finishes_without_blocking():
    rm = _RenameManager()
    await rm.start()
    # ensure the worker coroutine starts and waits for queue
    await asyncio.sleep(0)

    await asyncio.wait_for(rm.aclose(), timeout=0.5)

    assert rm._worker is None or rm._worker.done()
