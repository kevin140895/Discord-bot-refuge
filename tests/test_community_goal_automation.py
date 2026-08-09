from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import pytest

from services.community_goal_automation import (
    CommunityGoalAutomationConfig,
    CommunityGoalAutomationService,
)
from storage.community_goal_store import CommunityGoalStore
from storage.season_store import SeasonStore


class FirstRandom:
    def choice(self, seq: Sequence[Any]) -> Any:
        return seq[0]

    def randint(self, a: int, b: int) -> int:
        return a


async def _record_messages(
    store: SeasonStore,
    *,
    at: datetime,
    amount: int,
) -> None:
    await store.record(1, at=at, messages=amount)


def _config(**overrides: int) -> CommunityGoalAutomationConfig:
    values = {
        "cooldown_min_hours": 0,
        "cooldown_max_hours": 0,
        "duration_min_days": 2,
        "duration_max_days": 2,
        "minimum_observation_hours": 24,
    }
    values.update(overrides)
    return CommunityGoalAutomationConfig(**values)


@pytest.mark.asyncio
async def test_first_sync_only_schedules_then_due_sync_creates_adaptive_goal(tmp_path: Path):
    goals = CommunityGoalStore(tmp_path / "goals.json")
    seasons = SeasonStore(tmp_path / "seasons.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await _record_messages(
        seasons,
        at=now - timedelta(days=2),
        amount=2000,
    )

    service = CommunityGoalAutomationService(
        goal_store=goals,
        season_store_=seasons,
        random_source=FirstRandom(),
    )

    first = await service.sync(at=now, config=_config())
    assert first.created_goal is None
    assert first.next_goal_at == now.isoformat()

    second = await service.sync(at=now, config=_config())
    assert second.created_goal is not None
    created = second.created_goal
    assert created["metric_key"] == "messages"
    assert created["source"] == "automatic"
    assert created["created_by"] == 0
    assert created["target"] == 1600
    assert created["metadata"]["difficulty"] == "easy"
    assert created["metadata"]["difficulty_label"] == "Facile"
    assert created["metadata"]["duration_days"] == 2
    assert created["metadata"]["multiplier"] == 0.8


@pytest.mark.asyncio
async def test_category_without_measured_activity_is_never_invented(tmp_path: Path):
    goals = CommunityGoalStore(tmp_path / "goals.json")
    seasons = SeasonStore(tmp_path / "seasons.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    service = CommunityGoalAutomationService(
        goal_store=goals,
        season_store_=seasons,
        random_source=FirstRandom(),
    )

    await service.sync(at=now, config=_config())
    result = await service.sync(at=now, config=_config())

    assert result.created_goal is None
    assert await goals.list_goals() == []
    assert result.next_goal_at == now.isoformat()


@pytest.mark.asyncio
async def test_generator_waits_until_all_active_goals_are_finished(tmp_path: Path):
    goals = CommunityGoalStore(tmp_path / "goals.json")
    seasons = SeasonStore(tmp_path / "seasons.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await _record_messages(seasons, at=now - timedelta(days=2), amount=2000)
    manual = await goals.create_goal(
        metric_key="messages",
        target=100,
        baseline_total=0,
        created_by=42,
        created_at=now,
        ends_at=now + timedelta(days=1),
    )
    service = CommunityGoalAutomationService(
        goal_store=goals,
        season_store_=seasons,
        random_source=FirstRandom(),
    )

    active = await service.sync(at=now, config=_config())
    assert active.created_goal is None
    assert active.next_goal_at is None

    await goals.finish_goal(
        manual["id"],
        status="completed",
        final_progress=100,
        at=now + timedelta(hours=2),
    )
    after_finish = await service.sync(at=now + timedelta(hours=2), config=_config())
    assert after_finish.created_goal is None
    assert after_finish.next_goal_at == (now + timedelta(hours=2)).isoformat()


@pytest.mark.asyncio
async def test_last_category_is_avoided_when_another_measured_metric_exists(tmp_path: Path):
    goals = CommunityGoalStore(tmp_path / "goals.json")
    seasons = SeasonStore(tmp_path / "seasons.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await seasons.record(
        1,
        at=now - timedelta(days=2),
        xp_earned=4000,
        messages=2000,
    )
    await goals.set_automation_state(
        {
            "enabled_at": (now - timedelta(days=3)).isoformat(),
            "next_goal_at": now.isoformat(),
            "recent_metric_keys": ["xp"],
            "had_active_goal": False,
        }
    )
    service = CommunityGoalAutomationService(
        goal_store=goals,
        season_store_=seasons,
        random_source=FirstRandom(),
    )

    result = await service.sync(at=now, config=_config())

    assert result.created_goal is not None
    assert result.created_goal["metric_key"] == "messages"


@pytest.mark.asyncio
async def test_random_cooldown_and_duration_use_configured_bounds(tmp_path: Path):
    goals = CommunityGoalStore(tmp_path / "goals.json")
    seasons = SeasonStore(tmp_path / "seasons.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    service = CommunityGoalAutomationService(
        goal_store=goals,
        season_store_=seasons,
        random_source=FirstRandom(),
    )
    cfg = _config(
        cooldown_min_hours=12,
        cooldown_max_hours=36,
        duration_min_days=2,
        duration_max_days=5,
    )

    result = await service.sync(at=now, config=cfg)

    assert result.next_goal_at == (now + timedelta(hours=12)).isoformat()


@pytest.mark.asyncio
async def test_automation_state_survives_store_reload(tmp_path: Path):
    path = tmp_path / "goals.json"
    store = CommunityGoalStore(path)
    state = {
        "enabled_at": "2026-08-09T12:00:00+00:00",
        "next_goal_at": "2026-08-10T12:00:00+00:00",
        "recent_metric_keys": ["xp", "messages"],
        "had_active_goal": False,
    }
    await store.set_automation_state(state)

    reloaded = CommunityGoalStore(path)
    assert await reloaded.get_automation_state() == state
