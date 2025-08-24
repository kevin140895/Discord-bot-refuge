"""API call metering and JSONL logging."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import discord
import inspect
import json
import logging
import os
import time
from collections import deque, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple
from zoneinfo import ZoneInfo

import config
from utils.persistence import ensure_dir

# Context variable used to propagate command information
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


class APIMeter:
    """Collect and persist API call metrics."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.events: Deque[Tuple[float, Dict[str, Any]]] = deque()
        self.logger = logging.getLogger("api_meter")
        self.data_dir = Path(config.DATA_DIR)
        ensure_dir(self.data_dir)
        self.bot: Any | None = None
        self.writer_task: asyncio.Task | None = None
        self.summary_task: asyncio.Task | None = None
        self.alert_cooldowns: Dict[str, float] = {}
        self.alert_messages: Deque[Tuple[str, float]] = deque()

    # ------------------------------------------------------------------
    # Context helpers
    def set_context(self, cog: str | None, command: str | None) -> None:
        api_context.set({"cog": cog or "", "command": command or ""})

    def _apply_context(self, data: Dict[str, Any]) -> None:
        ctx = api_context.get() or {}
        data.setdefault("cog", ctx.get("cog") or None)
        data.setdefault("command", ctx.get("command") or None)
        if not data.get("caller"):
            # Fallback to stack inspection
            for frame in inspect.stack()[2:7]:  # skip current + record_call frame
                fname = frame.filename
                if "discord" in fname or "site-packages" in fname:
                    continue
                rel = os.path.relpath(fname, os.getcwd())
                data["caller"] = f"{rel}:{frame.lineno}"
                break

    # ------------------------------------------------------------------
    def record_call(self, ctx: APICallCtx) -> None:
        data = asdict(ctx)
        self._apply_context(data)
        data["ts"] = datetime.now(ZoneInfo("Europe/Paris")).isoformat()
        self.queue.put_nowait(data)
        now = time.time()
        self.events.append((now, data))
        cutoff = now - 3600  # keep last hour of data
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    # ------------------------------------------------------------------
    async def _writer_loop(self) -> None:
        buffer: List[Dict[str, Any]] = []
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=0.25)
                if item is None:
                    if buffer:
                        await asyncio.to_thread(self._flush, buffer)
                        buffer.clear()
                    break
                buffer.append(item)
                if len(buffer) >= 100:
                    await asyncio.to_thread(self._flush, buffer)
                    buffer.clear()
            except asyncio.TimeoutError:
                if buffer:
                    await asyncio.to_thread(self._flush, buffer)
                    buffer.clear()

    def _flush(self, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        now = datetime.now(ZoneInfo("Europe/Paris"))
        path = self.data_dir / f"api_metrics-{now:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for item in items:
                json.dump(item, f, ensure_ascii=False)
                f.write("\n")

    # ------------------------------------------------------------------
    def _calc_stats(self, window_min: int) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
        cutoff = time.time() - window_min * 60
        route_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        source_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for ts, ev in self.events:
            if ts < cutoff:
                continue
            key = f"{ev['method']} {ev['route']}"
            rs = route_stats[key]
            rs["calls"] += 1
            rs["errors"] += 1 if ev["status"] >= 400 else 0
            rs["429"] += 1 if ev["status"] == 429 else 0
            rs["slow"] += 1 if ev["duration_ms"] > config.API_SLOW_CALL_MS else 0
            rs["dur_ms"] += ev["duration_ms"]

            source = ev.get("caller") or f"{ev.get('cog') or ''}:{ev.get('command') or ''}".strip(":")
            ss = source_stats[source]
            ss["calls"] += 1
            ss["errors"] += 1 if ev["status"] >= 400 else 0
            ss["429"] += 1 if ev["status"] == 429 else 0
            ss["slow"] += 1 if ev["duration_ms"] > config.API_SLOW_CALL_MS else 0
            ss["dur_ms"] += ev["duration_ms"]
        return route_stats, source_stats

    def get_top_routes(self, window_min: int = 10, top: int = 10) -> List[Dict[str, Any]]:
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

    def get_top_sources(self, window_min: int = 10, top: int = 10) -> List[Dict[str, Any]]:
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
            routes = self.get_top_routes(10, 5)
            total = sum(r["calls"] for r in routes)
            errors = sum(r["errors"] for r in routes)
            too_many = sum(r["429"] for r in routes)
            avg = (
                sum(r["avg_ms"] * r["calls"] for r in routes) / total if total else 0.0
            )
            usage_pct = (total / config.API_BUDGET_PER_10MIN) * 100 if config.API_BUDGET_PER_10MIN else 0
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

    async def emit_alert(self, level: int, message: str, *, key: str, notify: bool = False) -> None:
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
        if self.writer_task is None:
            self.writer_task = asyncio.create_task(self._writer_loop())
        if self.summary_task is None:
            self.summary_task = asyncio.create_task(self._summary_loop())

    async def aclose(self) -> None:
        if self.writer_task:
            await self.queue.put(None)
            with contextlib.suppress(Exception):
                await self.writer_task
            self.writer_task = None
        if self.summary_task:
            self.summary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.summary_task
            self.summary_task = None


# Global instance
api_meter = APIMeter()


__all__ = ["APICallCtx", "api_meter"]
