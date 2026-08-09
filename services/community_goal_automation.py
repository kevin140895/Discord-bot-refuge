from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

from storage.community_goal_store import CommunityGoalStore, community_goal_store
from storage.season_store import SeasonStore, season_store
from utils.community_goals import COMMUNITY_GOAL_METRICS, CommunityGoalMetric
from utils.seasons import season_id_for


DIFFICULTY_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("easy", 0.80),
    ("normal", 1.00),
    ("ambitious", 1.20),
)
DIFFICULTY_LABELS = {
    "easy": "Facile",
    "normal": "Normal",
    "ambitious": "Ambitieux",
}

_TITLE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "xp": (
        "Élan collectif",
        "La montée du Refuge",
        "L’effort commun",
        "Une poussée d’énergie",
    ),
    "messages": (
        "Le Refuge s’anime",
        "Les voix du camp",
        "Chroniques du Refuge",
        "La place s’éveille",
    ),
    "vocal": (
        "Autour du grand feu",
        "Les veillées du Refuge",
        "Le cercle des voix",
        "Nuits au campement",
    ),
    "casino": (
        "La table est ouverte",
        "Fièvre au Casino",
        "Les dés du Refuge",
        "Une semaine de hasard",
    ),
}

_TARGET_GRANULARITY = {
    "xp": 100,
    "messages": 10,
    "vocal": 900,
    "casino": 5,
}


class RandomSource(Protocol):
    def choice(self, seq: Sequence[Any]) -> Any: ...

    def randint(self, a: int, b: int) -> int: ...


@dataclass(frozen=True, slots=True)
class CommunityGoalAutomationConfig:
    cooldown_min_hours: int = 12
    cooldown_max_hours: int = 36
    duration_min_days: int = 2
    duration_max_days: int = 5
    minimum_observation_hours: int = 24

    def __post_init__(self) -> None:
        if self.cooldown_min_hours < 0:
            raise ValueError("cooldown_min_hours must be >= 0")
        if self.cooldown_max_hours < self.cooldown_min_hours:
            raise ValueError("cooldown_max_hours must be >= cooldown_min_hours")
        if self.duration_min_days < 1:
            raise ValueError("duration_min_days must be >= 1")
        if self.duration_max_days < self.duration_min_days:
            raise ValueError("duration_max_days must be >= duration_min_days")
        if self.minimum_observation_hours < 1:
            raise ValueError("minimum_observation_hours must be >= 1")

    @classmethod
    def from_env(cls) -> "CommunityGoalAutomationConfig":
        return cls(
            cooldown_min_hours=_env_int("REFUGE_AUTO_GOAL_COOLDOWN_MIN_HOURS", 12),
            cooldown_max_hours=_env_int("REFUGE_AUTO_GOAL_COOLDOWN_MAX_HOURS", 36),
            duration_min_days=_env_int("REFUGE_AUTO_GOAL_DURATION_MIN_DAYS", 2),
            duration_max_days=_env_int("REFUGE_AUTO_GOAL_DURATION_MAX_DAYS", 5),
            minimum_observation_hours=_env_int(
                "REFUGE_AUTO_GOAL_MIN_OBSERVATION_HOURS", 24
            ),
        )


@dataclass(frozen=True, slots=True)
class MetricActivityRate:
    metric_key: str
    total: int
    observed_days: float
    daily_rate: float


@dataclass(frozen=True, slots=True)
class CommunityGoalAutomationResult:
    created_goal: dict[str, Any] | None
    next_goal_at: str | None
    changed: bool


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sum_season_field(payload: Mapping[str, Any], field: str) -> int:
    users = payload.get("users", {})
    if not isinstance(users, Mapping):
        return 0
    total = 0
    for user in users.values():
        if not isinstance(user, Mapping):
            continue
        try:
            total += int(user.get(field, 0))
        except (TypeError, ValueError):
            continue
    return max(0, total)


def _round_target(metric_key: str, raw_target: float) -> int:
    granularity = max(1, int(_TARGET_GRANULARITY.get(metric_key, 1)))
    rounded = int(round(max(1.0, raw_target) / granularity)) * granularity
    return max(granularity, rounded)


def _automatic_state(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, Mapping) else {}
    recent = source.get("recent_metric_keys", ())
    recent_keys = (
        [str(item) for item in recent if str(item)]
        if isinstance(recent, (list, tuple))
        else []
    )
    return {
        "enabled_at": str(source.get("enabled_at") or "") or None,
        "next_goal_at": str(source.get("next_goal_at") or "") or None,
        "recent_metric_keys": recent_keys[-3:],
        "had_active_goal": bool(source.get("had_active_goal", False)),
        "last_generated_at": str(source.get("last_generated_at") or "") or None,
        "last_goal_id": str(source.get("last_goal_id") or "") or None,
    }


class CommunityGoalAutomationService:
    """Generate one adaptive random community goal when the Refuge is idle."""

    def __init__(
        self,
        *,
        goal_store: CommunityGoalStore = community_goal_store,
        season_store_: SeasonStore = season_store,
        random_source: RandomSource | None = None,
    ) -> None:
        self.goal_store = goal_store
        self.season_store = season_store_
        self.random = random_source or secrets.SystemRandom()

    async def _activity_rates(
        self,
        *,
        at: datetime,
        config: CommunityGoalAutomationConfig,
    ) -> dict[str, MetricActivityRate]:
        current_season_id = season_id_for(at)
        payload = await self.season_store.get_season(current_season_id)
        if not isinstance(payload, Mapping):
            return {}

        started_at = _parse_timestamp(payload.get("started_at"))
        if started_at is None:
            tracking_started = await self.season_store.tracking_started_at()
            started_at = _parse_timestamp(tracking_started)
        if started_at is None:
            return {}

        elapsed_hours = max(
            float(config.minimum_observation_hours),
            (at - started_at).total_seconds() / 3600.0,
        )
        observed_days = elapsed_hours / 24.0
        rates: dict[str, MetricActivityRate] = {}
        for metric in COMMUNITY_GOAL_METRICS:
            total = _sum_season_field(payload, metric.season_field)
            if total <= 0:
                continue
            daily_rate = total / observed_days
            if daily_rate <= 0:
                continue
            rates[metric.key] = MetricActivityRate(
                metric_key=metric.key,
                total=total,
                observed_days=observed_days,
                daily_rate=daily_rate,
            )
        return rates

    def _choose_metric(
        self,
        rates: Mapping[str, MetricActivityRate],
        recent_metric_keys: Sequence[str],
    ) -> CommunityGoalMetric | None:
        eligible = [metric for metric in COMMUNITY_GOAL_METRICS if metric.key in rates]
        if not eligible:
            return None
        if len(eligible) > 1 and recent_metric_keys:
            last = str(recent_metric_keys[-1])
            without_last = [metric for metric in eligible if metric.key != last]
            if without_last:
                eligible = without_last
        return self.random.choice(tuple(eligible))

    def _schedule_next(
        self,
        *,
        now: datetime,
        config: CommunityGoalAutomationConfig,
    ) -> datetime:
        delay = self.random.randint(
            config.cooldown_min_hours,
            config.cooldown_max_hours,
        )
        return now + timedelta(hours=delay)

    async def sync(
        self,
        *,
        at: datetime | None = None,
        config: CommunityGoalAutomationConfig | None = None,
    ) -> CommunityGoalAutomationResult:
        now = _aware_utc(at)
        cfg = config or CommunityGoalAutomationConfig.from_env()
        stored = _automatic_state(await self.goal_store.get_automation_state())
        changed = False

        if stored["enabled_at"] is None:
            stored["enabled_at"] = now.isoformat()
            changed = True

        active_goals = await self.goal_store.list_goals(status="active")
        if active_goals:
            if not stored["had_active_goal"] or stored["next_goal_at"] is not None:
                stored["had_active_goal"] = True
                stored["next_goal_at"] = None
                changed = True
            if changed:
                await self.goal_store.set_automation_state(stored)
            return CommunityGoalAutomationResult(None, None, changed)

        if stored["had_active_goal"]:
            stored["had_active_goal"] = False
            stored["next_goal_at"] = self._schedule_next(now=now, config=cfg).isoformat()
            changed = True
            await self.goal_store.set_automation_state(stored)
            return CommunityGoalAutomationResult(
                None,
                stored["next_goal_at"],
                True,
            )

        due_at = _parse_timestamp(stored["next_goal_at"])
        if due_at is None:
            due_at = self._schedule_next(now=now, config=cfg)
            stored["next_goal_at"] = due_at.isoformat()
            changed = True
            await self.goal_store.set_automation_state(stored)
            return CommunityGoalAutomationResult(None, due_at.isoformat(), True)

        if now < due_at:
            return CommunityGoalAutomationResult(None, due_at.isoformat(), changed)

        rates = await self._activity_rates(at=now, config=cfg)
        metric = self._choose_metric(rates, stored["recent_metric_keys"])
        if metric is None:
            # No measurable activity yet: retry after another random cooldown
            # instead of inventing a target from an arbitrary floor.
            next_at = self._schedule_next(now=now, config=cfg)
            stored["next_goal_at"] = next_at.isoformat()
            await self.goal_store.set_automation_state(stored)
            return CommunityGoalAutomationResult(None, next_at.isoformat(), True)

        duration_days = self.random.randint(cfg.duration_min_days, cfg.duration_max_days)
        difficulty_key, multiplier = self.random.choice(DIFFICULTY_MULTIPLIERS)
        rate = rates[metric.key]
        target = _round_target(
            metric.key,
            rate.daily_rate * float(duration_days) * float(multiplier),
        )
        templates = _TITLE_TEMPLATES.get(metric.key, (metric.label,))
        title = str(self.random.choice(templates))
        baseline_total = await self._all_time_metric_total(metric)

        try:
            created = await self.goal_store.create_goal(
                metric_key=metric.key,
                target=target,
                baseline_total=baseline_total,
                created_by=0,
                created_at=now,
                ends_at=now + timedelta(days=duration_days),
                title=title,
                reward_text=None,
                source="automatic",
                metadata={
                    "difficulty": difficulty_key,
                    "difficulty_label": DIFFICULTY_LABELS[difficulty_key],
                    "duration_days": duration_days,
                    "daily_rate": round(rate.daily_rate, 4),
                    "observed_days": round(rate.observed_days, 4),
                    "multiplier": multiplier,
                },
                require_no_active=True,
            )
        except ValueError as exc:
            # A manual goal may have appeared between the idle check and the
            # atomic create. Treat that race as a normal pause, never as an
            # automation failure.
            if "active goal" not in str(exc):
                raise
            stored["had_active_goal"] = True
            stored["next_goal_at"] = None
            await self.goal_store.set_automation_state(stored)
            return CommunityGoalAutomationResult(None, None, True)

        recent = [*stored["recent_metric_keys"], metric.key][-3:]
        stored["recent_metric_keys"] = recent
        stored["last_generated_at"] = now.isoformat()
        stored["last_goal_id"] = str(created.get("id") or "") or None
        stored["had_active_goal"] = True
        stored["next_goal_at"] = None
        await self.goal_store.set_automation_state(stored)
        return CommunityGoalAutomationResult(created, None, True)

    async def _all_time_metric_total(self, metric: CommunityGoalMetric) -> int:
        total = 0
        for season_id in await self.season_store.list_seasons():
            payload = await self.season_store.get_season(season_id)
            if isinstance(payload, Mapping):
                total += _sum_season_field(payload, metric.season_field)
        return max(0, total)


community_goal_automation_service = CommunityGoalAutomationService()


__all__ = [
    "DIFFICULTY_LABELS",
    "DIFFICULTY_MULTIPLIERS",
    "CommunityGoalAutomationConfig",
    "CommunityGoalAutomationResult",
    "CommunityGoalAutomationService",
    "MetricActivityRate",
    "community_goal_automation_service",
]
