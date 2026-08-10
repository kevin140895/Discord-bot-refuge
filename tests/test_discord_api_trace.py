from __future__ import annotations

from types import SimpleNamespace

import pytest
from yarl import URL

from utils import discord_api_trace


class FakeLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def acquire(self, n: int = 1, bucket: str = "global") -> None:
        assert n == 1
        self.calls.append(bucket)


async def _run_attempt(
    trace,
    *,
    url: str,
    status: int,
    headers: dict[str, str] | None = None,
    content_length: int | None = None,
) -> None:
    trace_ctx = SimpleNamespace()
    start_params = SimpleNamespace(method="GET", url=URL(url), headers={})
    end_params = SimpleNamespace(
        method="GET",
        url=URL(url),
        headers={},
        response=SimpleNamespace(
            status=status,
            headers=headers or {},
            content_length=content_length,
        ),
    )
    await trace.on_request_start[0](None, trace_ctx, start_params)
    await trace.on_request_end[0](None, trace_ctx, end_params)


@pytest.mark.asyncio
async def test_trace_records_discord_response_and_rate_limit_headers(monkeypatch):
    limiter = FakeLimiter()
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    trace = discord_api_trace.create_discord_http_trace(limiter)

    await _run_attempt(
        trace,
        url="https://discord.com/api/v10/channels/123456789/messages/987654321",
        status=200,
        headers={
            "X-RateLimit-Bucket": "bucket-1",
            "X-RateLimit-Remaining": "4",
            "X-RateLimit-Reset": "1786400000.25",
        },
        content_length=321,
    )

    assert limiter.calls == ["global"]
    assert len(recorded) == 1
    call = recorded[0]
    assert call.lib == "discord.py"
    assert call.method == "GET"
    assert call.route == "/channels/{id}/messages/{id}"
    assert call.major_param == "channels:123456789"
    assert call.status == 200
    assert call.bucket == "bucket-1"
    assert call.ratelimit_remaining == 4
    assert call.ratelimit_reset == 1786400000.25
    assert call.retry_after_ms == 0
    assert call.size_bytes == 321


@pytest.mark.asyncio
async def test_trace_counts_403_and_429_retry_as_real_attempts(monkeypatch):
    limiter = FakeLimiter()
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    trace = discord_api_trace.create_discord_http_trace(limiter)
    url = "https://discord.com/api/v10/guilds/123456789/members/987654321"

    await _run_attempt(trace, url=url, status=403)
    await _run_attempt(
        trace,
        url=url,
        status=429,
        headers={"Retry-After": "1.25", "X-RateLimit-Remaining": "0"},
    )
    # Simulate discord.py's next HTTP attempt after handling the 429.
    await _run_attempt(trace, url=url, status=200)

    assert limiter.calls == ["global", "global", "global"]
    assert [call.status for call in recorded] == [403, 429, 200]
    assert recorded[1].retry_after_ms == 1250
    assert recorded[1].ratelimit_remaining == 0
    assert all(call.route == "/guilds/{id}/members/{id}" for call in recorded)


@pytest.mark.asyncio
async def test_trace_redacts_webhook_and_interaction_tokens(monkeypatch):
    limiter = FakeLimiter()
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    trace = discord_api_trace.create_discord_http_trace(limiter)
    secret = "super-secret-interaction-token"

    await _run_attempt(
        trace,
        url=(
            "https://discord.com/api/v10/webhooks/123456789/"
            f"{secret}/messages/@original"
        ),
        status=200,
    )

    assert recorded[0].route == "/webhooks/{id}/{token}/messages/@original"
    assert recorded[0].major_param == "webhooks:123456789"
    assert secret not in recorded[0].route


@pytest.mark.asyncio
async def test_trace_records_transport_failures_as_errors(monkeypatch):
    limiter = FakeLimiter()
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    trace = discord_api_trace.create_discord_http_trace(limiter)
    url = URL("https://discord.com/api/v10/channels/123456789/messages")
    trace_ctx = SimpleNamespace()

    await trace.on_request_start[0](
        None,
        trace_ctx,
        SimpleNamespace(method="POST", url=url, headers={}),
    )
    await trace.on_request_exception[0](
        None,
        trace_ctx,
        SimpleNamespace(
            method="POST",
            url=url,
            headers={},
            exception=OSError("connection reset"),
        ),
    )

    assert limiter.calls == ["global"]
    assert len(recorded) == 1
    assert recorded[0].status == 599
    assert recorded[0].route == "/channels/{id}/messages"


@pytest.mark.asyncio
async def test_trace_ignores_non_discord_http(monkeypatch):
    limiter = FakeLimiter()
    recorded = []
    monkeypatch.setattr(discord_api_trace.api_meter, "record_call", recorded.append)
    trace = discord_api_trace.create_discord_http_trace(limiter)

    await _run_attempt(
        trace,
        url="https://example.com/api/v10/channels/123/messages",
        status=200,
    )

    assert limiter.calls == []
    assert recorded == []
