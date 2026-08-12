"""API call metering and batched JSON persistence."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import discord
import json
import logging
import os
import time
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple
from zoneinfo import ZoneInfo

import config
from utils.persistence import ensure_dir

_PARIS_TZ = ZoneInfo("Europe/Paris")

# Context variable used only when a caller explicitly provides useful command
# information. APIMeter deliberately does not inspect Python stacks on REST calls.
api_context: contextvars.ContextVar[Dict[str, str] | None] = contextvars.ContextVar(
    "api_context", default=None
)


@dataclass
class APICallCtx:
    """Context information for a single API call."""

    lib: str
    method: str
    route: str
    major_param: str | None
    status: int
    duration_ms: int
    retry_after_ms: int
    bucket: str | None
    ratelimit_remaining: int | None
    ratelimit_reset: float | None
    error_code: int | None
    cog: str | None = None
    command: str | None = None
    caller: str | None = None
    size_bytes: int | None = None
    started_at: float | None = None


def _new_totals() -> defaultdict[str, defaultdict[str, float]]:
    return defaultdict(lambda: defaultdict(float))


class APIMeter:
    """Collect Discord REST metrics in RAM and persist them in batches."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue()
        self.events: Deque[Tuple[float, Dict[str, Any]]] = deque()
        self.logger = logging.getLogger("api_meter")
        self.data_dir = Path(config.DATA_DIR)
        ensure_dir(self.data_dir)
        self.bot: Any | None = None
        self.writer_task: asyncio.Task | None = None
        self.summary_task: asyncio.Task | None = None
        self.alert_cooldowns: Dict[str, float] = {}
        self.alert_messages: Deque[Tuple[str, float]] = deque()

        # Public aliases for the active calendar month's aggregate dictionaries.
        self.route_totals = _new_totals()
        self.source_totals = _new_totals()
        self.current_month: str | None = None

        # Month-keyed in-memory buckets make rollover deterministic. A late
        # request from the previous month is written back to that month's file
        # instead of contaminating the new month.
        self._month_route_totals: Dict[
            str, defaultdict[str, defaultdict[str, float]]
        ] = {}
        self._month_source_totals: Dict[
            str, defaultdict[str, defaultdict[str, float]]
        ] = {}
        self._dirty_months: set[str] = set()
        self._save_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Context helpers
    def set_context(
        self, cog: str | None, command: str | None
    ) -> contextvars.Token[Dict[str, str] | None]:
        """Set optional explicit command context for the current async context."""

        return api_context.set({"cog": cog or "", "command": command or ""})

    def reset_context(
        self, token: contextvars.Token[Dict[str, str] | None]
    ) -> None:
        """Restore the explicit API context previously returned by set_context."""

        api_context.reset(token)

    def _apply_context(self, data: Dict[str, Any]) -> None:
        ctx = api_context.get() or {}
        if not data.get("cog"):
            data["cog"] = ctx.get("cog") or None
        if not data.get("command"):
            data["command"] = ctx.get("command") or None

    # ------------------------------------------------------------------
    def record_call(self, ctx: APICallCtx) -> None:
        """Queue one REST event without disk I/O, stack walking, or aggregation."""

        occurred_at = (
            datetime.fromtimestamp(ctx.started_at, _PARIS_TZ)
            if ctx.started_at is not None
            else datetime.now(_PARIS_TZ)
        )
        data: Dict[str, Any] = {
            "lib": ctx.lib,
            "method": ctx.method,
            "route": ctx.route,
            "major_param": ctx.major_param,
            "status": ctx.status,
            "duration_ms": ctx.duration_ms,
            "retry_after_ms": ctx.retry_after_ms,
            "bucket": ctx.bucket,
            "ratelimit_remaining": ctx.ratelimit_remaining,
            "ratelimit_reset": ctx.ratelimit_reset,
            "error_code": ctx.error_code,
            "cog": ctx.cog,
            "command": ctx.command,
            "caller": ctx.caller,
            "size_bytes": ctx.size_bytes,
            "ts": occurred_at.isoformat(),
        }
        self._apply_context(data)
        self.queue.put_nowait(data)

    # ------------------------------------------------------------------
    def _stats_path(
        self, dt: datetime | None = None, *, month: str | None = None
    ) -> Path:
        if month is None:
            dt = dt or datetime.now(_PARIS_TZ)
            month = f"{dt:%Y-%m}"
        return self.data_dir / f"api_metrics-{month}.json"

    def _write_stats(
        self,
        month: str,
        routes: Dict[str, Dict[str, float]],
        sources: Dict[str, Dict[str, float]],
    ) -> None:
        path = self._stats_path(month=month)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"routes": routes, "sources": sources}, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _read_aggregates(
        self, month: str
    ) -> tuple[
        defaultdict[str, defaultdict[str, float]],
        defaultdict[str, defaultdict[str, float]],
    ]:
        routes = _new_totals()
        sources = _new_totals()
        path = self._stats_path(month=month)
        if not path.exists():
            return routes, sources

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for key, stats in data.get("routes", {}).items():
                routes[key].update(stats)
            for key, stats in data.get("sources", {}).items():
                sources[key].update(stats)
        except Exception:
            self.logger.exception("failed to load api metrics for month=%s", month)
        return routes, sources

    def _set_active_month(
        self,
        month: str,
        routes: defaultdict[str, defaultdict[str, float]],
        sources: defaultdict[str, defaultdict[str, float]],
    ) -> None:
        self.current_month = month
        self.route_totals = routes
        self.source_totals = sources

    def _load_aggregates(self, dt: datetime | None = None) -> None:
        """Load the current month at startup.

        Other months are loaded lazily by the writer if a late event targets
        them. This method is intentionally synchronous because it runs once
        before the background worker starts.
        """

        dt = dt or datetime.now(_PARIS_TZ)
        month = f"{dt:%Y-%m}"
        routes, sources = self._read_aggregates(month)
        self._month_route_totals = {month: routes}
        self._month_source_totals = {month: sources}
        self._dirty_months.clear()
        self._set_active_month(month, routes, sources)

    async def _ensure_month_loaded(
        self, month: str
    ) -> tuple[
        defaultdict[str, defaultdict[str, float]],
        defaultdict[str, defaultdict[str, float]],
    ]:
        routes = self._month_route_totals.get(month)
        sources = self._month_source_totals.get(month)
        if routes is not None and sources is not None:
            return routes, sources

        routes, sources = await asyncio.to_thread(self._read_aggregates, month)
        self._month_route_totals[month] = routes
        self._month_source_totals[month] = sources
        return routes, sources

    @staticmethod
    def _event_datetime(data: Dict[str, Any]) -> datetime:
        ts = data.get("ts")
        if isinstance(ts, str):
            try:
                parsed = datetime.fromisoformat(ts)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=_PARIS_TZ)
                return parsed.astimezone(_PARIS_TZ)
            except ValueError:
                pass
        return datetime.now(_PARIS_TZ)

    @staticmethod
    def _source_key(data: Dict[str, Any]) -> str:
        caller = data.get("caller")
        if caller:
            return str(caller)
        explicit = (
            f"{data.get('cog') or ''}:{data.get('command') or ''}".strip(":")
        )
        return explicit or "unknown"

    async def _ingest_event(self, data: Dict[str, Any]) -> None:
        """Move one queued event into the in-memory windows and month bucket."""

        event_dt = self._event_datetime(data)
        month = f"{event_dt:%Y-%m}"
        routes, sources = await self._ensure_month_loaded(month)

        # Keep rolling-window data in RAM only.
        now = time.time()
        self.events.append((now, data))
        cutoff = now - 3600
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

        route_key = f"{data['method']} {data['route']}"
        rs = routes[route_key]
        rs["calls"] += 1
        rs["errors"] += 1 if data["status"] >= 400 else 0
        rs["429"] += 1 if data["status"] == 429 else 0
        rs["slow"] += (
            1 if data["duration_ms"] > config.API_SLOW_CALL_MS else 0
        )
        rs["dur_ms"] += data["duration_ms"]

        source = self._source_key(data)
        ss = sources[source]
        ss["calls"] += 1
        ss["errors"] += 1 if data["status"] >= 400 else 0
        ss["429"] += 1 if data["status"] == 429 else 0
        ss["slow"] += (
            1 if data["duration_ms"] > config.API_SLOW_CALL_MS else 0
        )
        ss["dur_ms"] += data["duration_ms"]

        self._dirty_months.add(month)

        # Never roll backwards for a late previous-month response.
        if self.current_month is None or month > self.current_month:
            self._set_active_month(month, routes, sources)

    async def _save_aggregates_async(self) -> None:
        """Persist dirty month buckets atomically, using an explicit month path."""

        async with self._save_lock:
            for month in sorted(tuple(self._dirty_months)):
                routes = self._month_route_totals.get(month)
                sources = self._month_source_totals.get(month)
                if routes is None or sources is None:
                    self._dirty_months.discard(month)
                    continue

                route_snapshot = {k: dict(v) for k, v in routes.items()}
                source_snapshot = {k: dict(v) for k, v in sources.items()}
                try:
                    await asyncio.to_thread(
                        self._write_stats,
                        month,
                        route_snapshot,
                        source_snapshot,
                    )
                except Exception:
                    self.logger.exception(
                        "failed to save api metrics for month=%s", month
                    )
                    continue
                self._dirty_months.discard(month)

    # ------------------------------------------------------------------
    async def _writer_loop(self) -> None:
        raw_buffer: List[Dict[str, Any]] = []
        interval = max(
            1.0, float(getattr(config, "API_METER_PERSIST_INTERVAL_SECONDS", 30))
        )
        next_persist = time.monotonic() + interval

        while True:
            timeout = max(0.0, next_persist - time.monotonic())
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                item = ...

            if item is ...:
                if raw_buffer:
                    try:
                        await asyncio.to_thread(self._flush, raw_buffer)
                    except Exception:
                        self.logger.exception("failed to flush api metric events")
                    else:
                        raw_buffer.clear()
                await self._save_aggregates_async()
                next_persist = time.monotonic() + interval
                continue

            if item is None:
                if raw_buffer:
                    try:
                        await asyncio.to_thread(self._flush, raw_buffer)
                    except Exception:
                        self.logger.exception(
                            "failed to flush api metric events during shutdown"
                        )
                    else:
                        raw_buffer.clear()
                await self._save_aggregates_async()
                break

            await self._ingest_event(item)
            raw_buffer.append(item)

    def _flush(self, items: List[Dict[str, Any]]) -> None:
        """Append raw events to the JSONL file matching each event's own day."""

        if not items:
            return

        by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in items:
            day = f"{self._event_datetime(item):%Y-%m-%d}"
            by_day[day].append(item)

        for day, day_items in by_day.items():
            path = self.data_dir / f"api_metrics-{day}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                for item in day_items:
                    json.dump(item, f, ensure_ascii=False)
                    f.write("\n")

    # ------------------------------------------------------------------
    def _calc_stats(
        self, window_min: int
    ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
        cutoff = time.time() - window_min * 60
        route_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        source_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for ts, ev in self.events:
            if ts < cutoff:
                continue
            key = f"{ev['method']} {ev['route']}"
            rs = route_stats[key]
            rs["calls"] += 1
            rs["errors"] += 1 if ev["status"] >= 400 else 0
            rs["429"] += 1 if ev["status"] == 429 else 0
            rs["slow"] += (
                1 if ev["duration_ms"] > config.API_SLOW_CALL_MS else 0
            )
            rs["dur_ms"] += ev["duration_ms"]

            source = self._source_key(ev)
            ss = source_stats[source]
            ss["calls"] += 1
            ss["errors"] += 1 if ev["status"] >= 400 else 0
            ss["429"] += 1 if ev["status"] == 429 else 0
            ss["slow"] += (
                1 if ev["duration_ms"] > config.API_SLOW_CALL_MS else 0
            )
            ss["dur_ms"] += ev["duration_ms"]
        return route_stats, source_stats

    def get_window_totals(self, window_min: int = 10) -> Dict[str, float | int]:
        """Return totals for every route observed inside the time window."""
        route_stats, _ = self._calc_stats(window_min)
        calls = sum(stats["calls"] for stats in route_stats.values())
        errors = sum(stats["errors"] for stats in route_stats.values())
        too_many = sum(stats["429"] for stats in route_stats.values())
        duration_ms = sum(stats["dur_ms"] for stats in route_stats.values())
        avg_ms = duration_ms / calls if calls else 0.0
        return {
            "calls": int(calls),
            "errors": int(errors),
            "429": int(too_many),
            "avg_ms": avg_ms,
        }

    def get_top_routes(
        self, window_min: int = 10, top: int = 10
    ) -> List[Dict[str, Any]]:
        route_stats, _ = self._calc_stats(window_min)
        out: List[Dict[str, Any]] = []
        for key, stats in route_stats.items():
            calls = stats["calls"]
            avg = stats["dur_ms"] / calls if calls else 0.0
            out.append(
                {
                    "route": key,
                    "calls": int(calls),
                    "errors": int(stats["errors"]),
                    "429": int(stats["429"]),
                    "slow": int(stats["slow"]),
                    "avg_ms": avg,
                }
            )
        out.sort(key=lambda x: x["calls"], reverse=True)
        return out[:top]

    def get_top_sources(
        self, window_min: int = 10, top: int = 10
    ) -> List[Dict[str, Any]]:
        _, source_stats = self._calc_stats(window_min)
        out: List[Dict[str, Any]] = []
        for key, stats in source_stats.items():
            calls = stats["calls"]
            avg = stats["dur_ms"] / calls if calls else 0.0
            out.append(
                {
                    "source": key or "unknown",
                    "calls": int(calls),
                    "errors": int(stats["errors"]),
                    "429": int(stats["429"]),
                    "slow": int(stats["slow"]),
                    "avg_ms": avg,
                }
            )
        out.sort(key=lambda x: x["calls"], reverse=True)
        return out[:top]

    def get_active_alerts(self) -> List[str]:
        cutoff = time.time() - 300
        return [msg for msg, ts in self.alert_messages if ts >= cutoff]

    # ------------------------------------------------------------------
    async def _summary_loop(self) -> None:
        while True:
            await asyncio.sleep(config.API_REPORT_INTERVAL_MIN * 60)
            totals = self.get_window_totals(10)
            total = int(totals["calls"])
            errors = int(totals["errors"])
            too_many = int(totals["429"])
            avg = float(totals["avg_ms"])
            usage_pct = (
                (total / config.API_BUDGET_PER_10MIN) * 100
                if config.API_BUDGET_PER_10MIN
                else 0
            )
            self.logger.info(
                "api_summary window=10min calls=%d errors=%d 429=%d avg_ms=%.1f usage=%.1f%%",
                total,
                errors,
                too_many,
                avg,
                usage_pct,
            )
            if too_many or usage_pct >= config.API_SOFT_LIMIT_PCT:
                await self.emit_alert(
                    logging.WARNING,
                    f"api.soft_limit usage={usage_pct:.0f}% calls={total} 429={too_many}",
                    key="soft",
                )
            if too_many or usage_pct >= config.API_HARD_LIMIT_PCT:
                await self.emit_alert(
                    logging.ERROR,
                    f"api.hard_limit usage={usage_pct:.0f}% 429={too_many}",
                    key="hard",
                    notify=True,
                )

    async def emit_alert(
        self, level: int, message: str, *, key: str, notify: bool = False
    ) -> None:
        now = time.time()
        if now - self.alert_cooldowns.get(key, 0) < 300:
            return
        self.alert_cooldowns[key] = now
        self.alert_messages.append((message, now))
        self.logger.log(level, message)
        if notify and self.bot and config.BOT_ALERTS_CHANNEL_ID:
            channel = self.bot.get_channel(config.BOT_ALERTS_CHANNEL_ID)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                try:
                    await channel.send(f"⚠️ {message}")
                except Exception:
                    self.logger.exception("failed to send alert message")
            else:
                self.logger.warning(
                    "Alerts channel %s missing or not messageable",
                    config.BOT_ALERTS_CHANNEL_ID,
                )

    # ------------------------------------------------------------------
    async def start(self, bot: Any) -> None:
        self.bot = bot
        if self.current_month is None:
            self._load_aggregates()
        if self.writer_task is None or self.writer_task.done():
            if self.writer_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    self.writer_task.result()
            self.writer_task = asyncio.create_task(self._writer_loop())
        if self.summary_task is None or self.summary_task.done():
            if self.summary_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    self.summary_task.result()
            self.summary_task = asyncio.create_task(self._summary_loop())

    async def aclose(self) -> None:
        if self.writer_task:
            writer_task = self.writer_task
            if not writer_task.done():
                await self.queue.put(None)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer_task
            self.writer_task = None
        if self.summary_task:
            summary_task = self.summary_task
            if not summary_task.done():
                summary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await summary_task
            self.summary_task = None

        # If the writer was already dead, keep shutdown persistence best-effort.
        await self._save_aggregates_async()


# Global instance
api_meter = APIMeter()


__all__ = ["APICallCtx", "api_meter"]
