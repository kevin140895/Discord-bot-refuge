"""Central Discord REST tracing for API metrics and global throttling."""

from __future__ import annotations

import re
import time
from typing import Any

import aiohttp

from utils.api_meter import APICallCtx, api_meter

_API_VERSION_RE = re.compile(r"^/api/v\d+")
_MAJOR_RESOURCES = {"channels", "guilds", "webhooks"}


def _is_discord_api_url(url: Any) -> bool:
    host = (getattr(url, "host", None) or "").lower()
    path = getattr(url, "path", "") or ""
    is_discord_host = (
        host == "discord.com"
        or host.endswith(".discord.com")
        or host == "discordapp.com"
        or host.endswith(".discordapp.com")
    )
    return is_discord_host and path.startswith("/api/")


def _normalise_route(url: Any) -> tuple[str, str | None]:
    """Return a low-cardinality route and its first Discord major parameter.

    Numeric resource IDs are removed from the route key so per-route metrics do
    not explode in cardinality. Webhook/interaction tokens are always redacted.
    The actual first channel/guild/webhook ID is retained separately as the
    ``major_param`` field expected by :class:`APICallCtx`.
    """

    path = getattr(url, "path", "") or "/"
    path = _API_VERSION_RE.sub("", path) or "/"
    parts = [part for part in path.split("/") if part]
    normalised: list[str] = []
    major_param: str | None = None

    index = 0
    while index < len(parts):
        part = parts[index]
        normalised.append(part)

        if part in _MAJOR_RESOURCES and index + 1 < len(parts):
            resource_id = parts[index + 1]
            if major_param is None:
                major_param = f"{part}:{resource_id}"
            normalised.append("{id}")
            index += 2

            if part == "webhooks" and index < len(parts):
                # Both normal webhooks and interaction callbacks put a secret
                # token directly after the webhook/application ID.
                normalised.append("{token}")
                index += 1
            continue

        if part.isdigit():
            normalised[-1] = "{id}"

        index += 1

    return "/" + "/".join(normalised), major_param


def _header_int(headers: Any, name: str) -> int | None:
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _header_float(headers: Any, name: str) -> float | None:
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _retry_after_ms(headers: Any) -> int:
    retry_after = _header_float(headers, "Retry-After")
    if retry_after is None or retry_after < 0:
        return 0
    return int(retry_after * 1000)


def _duration_ms(trace_config_ctx: Any) -> int:
    started_at = getattr(trace_config_ctx, "discord_api_started_at", None)
    if started_at is None:
        return 0
    return max(0, int((time.perf_counter() - started_at) * 1000))


def create_discord_http_trace(limiter: Any) -> aiohttp.TraceConfig:
    """Create the trace used by discord.py's shared REST ``ClientSession``.

    The global limiter is a local burst guard only. discord.py remains
    responsible for Discord's route/bucket rate-limit semantics and retries.
    Each actual HTTP attempt is recorded, including a 429 followed by a retry.
    """

    trace = aiohttp.TraceConfig()

    async def on_request_start(
        session: aiohttp.ClientSession,
        trace_config_ctx: Any,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        del session
        enabled = _is_discord_api_url(params.url)
        trace_config_ctx.discord_api_enabled = enabled
        if not enabled:
            return

        # Exclude local throttling time from measured Discord API latency.
        await limiter.acquire(bucket="global")
        trace_config_ctx.discord_api_started_at = time.perf_counter()

    async def on_request_end(
        session: aiohttp.ClientSession,
        trace_config_ctx: Any,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        del session
        if not getattr(trace_config_ctx, "discord_api_enabled", False):
            return

        response = params.response
        headers = response.headers
        route, major_param = _normalise_route(params.url)
        api_meter.record_call(
            APICallCtx(
                lib="discord.py",
                method=params.method,
                route=route,
                major_param=major_param,
                status=response.status,
                duration_ms=_duration_ms(trace_config_ctx),
                retry_after_ms=_retry_after_ms(headers),
                bucket=headers.get("X-RateLimit-Bucket"),
                ratelimit_remaining=_header_int(headers, "X-RateLimit-Remaining"),
                ratelimit_reset=_header_float(headers, "X-RateLimit-Reset"),
                error_code=None,
                size_bytes=getattr(response, "content_length", None),
            )
        )

    async def on_request_exception(
        session: aiohttp.ClientSession,
        trace_config_ctx: Any,
        params: aiohttp.TraceRequestExceptionParams,
    ) -> None:
        del session
        if not getattr(trace_config_ctx, "discord_api_enabled", False):
            return

        route, major_param = _normalise_route(params.url)
        exception = params.exception
        status = getattr(exception, "status", None)
        if not isinstance(status, int) or status <= 0:
            # Keep transport failures visible in APIMeter's >=400 error totals.
            status = 599
        error_code = getattr(exception, "code", None)
        if not isinstance(error_code, int):
            error_code = None

        api_meter.record_call(
            APICallCtx(
                lib="discord.py",
                method=params.method,
                route=route,
                major_param=major_param,
                status=status,
                duration_ms=_duration_ms(trace_config_ctx),
                retry_after_ms=0,
                bucket=None,
                ratelimit_remaining=None,
                ratelimit_reset=None,
                error_code=error_code,
            )
        )

    trace.on_request_start.append(on_request_start)
    trace.on_request_end.append(on_request_end)
    trace.on_request_exception.append(on_request_exception)
    return trace


__all__ = ["create_discord_http_trace"]
