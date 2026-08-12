from types import SimpleNamespace

import pytest
from yarl import URL

from utils import discord_api_trace


class FakeLimiter:
    async def acquire(self, n: int = 1, bucket: str = "global") -> None:
        assert n == 1
        assert bucket == "global"


@pytest.mark.asyncio
async def test_trace_propagates_request_start_wall_clock(monkeypatch):
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    monkeypatch.setattr(discord_api_trace.time, "time", lambda: 1788213599.25)

    trace = discord_api_trace.create_discord_http_trace(FakeLimiter())
    url = URL("https://discord.com/api/v10/channels/123/messages")
    trace_ctx = SimpleNamespace()

    await trace.on_request_start[0](
        None,
        trace_ctx,
        SimpleNamespace(method="GET", url=url, headers={}),
    )
    await trace.on_request_end[0](
        None,
        trace_ctx,
        SimpleNamespace(
            method="GET",
            url=url,
            headers={},
            response=SimpleNamespace(status=200, headers={}, content_length=None),
        ),
    )

    assert len(recorded) == 1
    assert recorded[0].started_at == 1788213599.25
