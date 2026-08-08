from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class CommunityGoalMetric:
    key: str
    label: str
    season_field: str
    emoji: str
    input_unit: str

    def to_base_value(self, value: int) -> int:
        amount = max(0, int(value))
        if self.key == "vocal":
            return amount * 3600
        return amount


COMMUNITY_GOAL_METRICS: tuple[CommunityGoalMetric, ...] = (
    CommunityGoalMetric("xp", "XP collective", "xp_earned", "⭐", "XP"),
    CommunityGoalMetric("messages", "Messages", "messages", "💬", "messages"),
    CommunityGoalMetric("vocal", "Temps vocal", "voice_seconds", "🎧", "heures"),
    CommunityGoalMetric("casino", "Paris casino", "casino_bets", "🎰", "paris"),
)
COMMUNITY_GOAL_METRICS_BY_KEY = {
    metric.key: metric for metric in COMMUNITY_GOAL_METRICS
}


def aggregate_metric_total(
    season_payloads: Iterable[dict[str, Any]],
    field: str,
) -> int:
    """Sum one user metric across multiple seasonal snapshots."""

    total = 0
    for season in season_payloads:
        if not isinstance(season, dict):
            continue
        users = season.get("users", {})
        if not isinstance(users, dict):
            continue
        for payload in users.values():
            if not isinstance(payload, dict):
                continue
            try:
                total += int(payload.get(field, 0))
            except (TypeError, ValueError):
                continue
    return total


def goal_progress(current_total: int, baseline_total: int, target: int) -> int:
    """Return prospective progress since goal creation, clamped at zero."""

    if target <= 0:
        return 0
    return max(0, int(current_total) - int(baseline_total))


def progress_percent(progress: int, target: int) -> int:
    if target <= 0:
        return 0
    return min(100, max(0, int(progress * 100 / target)))


def progress_bar(progress: int, target: int, width: int = 10) -> str:
    if width <= 0:
        return ""
    ratio = 0.0 if target <= 0 else min(1.0, max(0.0, progress / target))
    filled = min(width, max(0, int(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def format_goal_value(metric_key: str, value: int) -> str:
    value = max(0, int(value))
    if metric_key == "xp":
        return f"{value} XP"
    if metric_key == "messages":
        return f"{value} messages"
    if metric_key == "vocal":
        hours, remainder = divmod(value, 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes:02d}m"
    if metric_key == "casino":
        return f"{value} paris"
    return str(value)


__all__ = [
    "COMMUNITY_GOAL_METRICS",
    "COMMUNITY_GOAL_METRICS_BY_KEY",
    "CommunityGoalMetric",
    "aggregate_metric_total",
    "format_goal_value",
    "goal_progress",
    "progress_bar",
    "progress_percent",
]
