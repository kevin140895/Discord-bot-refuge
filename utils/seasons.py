from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from utils.timezones import PARIS_TZ


MONTH_NAMES_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


@dataclass(frozen=True, slots=True)
class SeasonMetric:
    key: str
    label: str
    field: str
    unit: str


SEASON_METRICS: tuple[SeasonMetric, ...] = (
    SeasonMetric("xp", "XP gagnée", "xp_earned", "XP"),
    SeasonMetric("messages", "Messages", "messages", "messages"),
    SeasonMetric("vocal", "Temps vocal", "voice_seconds", "secondes"),
    SeasonMetric("casino", "Casino net", "casino_net", "XP net"),
)
SEASON_METRICS_BY_KEY = {metric.key: metric for metric in SEASON_METRICS}
SEASON_FIELDS = frozenset(
    {metric.field for metric in SEASON_METRICS} | {"casino_bets"}
)

# Staff grants should not influence a competitive XP season.
EXCLUDED_XP_SOURCES = frozenset({"don_xp"})


def _as_paris(dt: datetime | None = None) -> datetime:
    current = dt or datetime.now(PARIS_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=PARIS_TZ)
    return current.astimezone(PARIS_TZ)


def season_id_for(dt: datetime | None = None) -> str:
    """Return the calendar-month season identifier in Europe/Paris."""

    current = _as_paris(dt)
    return f"{current.year:04d}-{current.month:02d}"


def parse_season_id(season_id: str) -> tuple[int, int]:
    """Validate and parse a ``YYYY-MM`` season identifier."""

    try:
        year_text, month_text = season_id.split("-", 1)
        year = int(year_text)
        month = int(month_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("season_id must use YYYY-MM") from exc
    if len(year_text) != 4 or len(month_text) != 2 or not 1 <= month <= 12:
        raise ValueError("season_id must use YYYY-MM")
    return year, month


def season_bounds(season_id: str) -> tuple[datetime, datetime]:
    """Return inclusive start and exclusive end for one Paris calendar month."""

    year, month = parse_season_id(season_id)
    start = datetime(year, month, 1, tzinfo=PARIS_TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=PARIS_TZ)
    else:
        end = datetime(year, month + 1, 1, tzinfo=PARIS_TZ)
    return start, end


def season_label(season_id: str) -> str:
    year, month = parse_season_id(season_id)
    return f"{MONTH_NAMES_FR[month - 1].capitalize()} {year}"


def should_count_xp_source(source: str, delta: int) -> bool:
    return delta > 0 and source not in EXCLUDED_XP_SOURCES


def split_interval_by_season(
    started_at: datetime,
    ended_at: datetime,
) -> list[tuple[str, int]]:
    """Split a voice interval across Paris month boundaries."""

    start = _as_paris(started_at)
    end = _as_paris(ended_at)
    if end <= start:
        return []

    parts: list[tuple[str, int]] = []
    cursor = start
    while cursor < end:
        season_id = season_id_for(cursor)
        _, season_end = season_bounds(season_id)
        chunk_end = min(end, season_end)
        seconds = int((chunk_end - cursor).total_seconds())
        if seconds > 0:
            parts.append((season_id, seconds))
        cursor = chunk_end
    return parts


def format_metric_value(metric_key: str, value: int) -> str:
    if metric_key == "xp":
        return f"{value} XP"
    if metric_key == "messages":
        return f"{value} messages"
    if metric_key == "vocal":
        hours, remainder = divmod(max(0, value), 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes:02d}m"
    if metric_key == "casino":
        return f"{value:+d} XP net"
    return str(value)


def rank_rows(
    users: dict[str, dict[str, int]],
    field: str,
) -> list[tuple[str, int]]:
    """Return positive/meaningful rows sorted descending for one metric."""

    rows: list[tuple[str, int]] = []
    for user_id, payload in users.items():
        if not isinstance(payload, dict):
            continue
        try:
            value = int(payload.get(field, 0))
        except (TypeError, ValueError):
            continue
        if field != "casino_net" and value <= 0:
            continue
        if field == "casino_net" and int(payload.get("casino_bets", 0) or 0) <= 0:
            continue
        rows.append((str(user_id), value))
    rows.sort(key=lambda row: (row[1], row[0]), reverse=True)
    return rows


__all__ = [
    "SEASON_METRICS",
    "SEASON_METRICS_BY_KEY",
    "SEASON_FIELDS",
    "EXCLUDED_XP_SOURCES",
    "SeasonMetric",
    "format_metric_value",
    "parse_season_id",
    "rank_rows",
    "season_bounds",
    "season_id_for",
    "season_label",
    "should_count_xp_source",
    "split_interval_by_season",
]
