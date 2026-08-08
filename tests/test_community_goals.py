from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from storage.community_goal_store import CommunityGoalStore
from utils.community_goals import (
    COMMUNITY_GOAL_METRICS_BY_KEY,
    aggregate_metric_total,
    format_goal_value,
    goal_progress,
    progress_bar,
    progress_percent,
)


def test_goal_metric_units_and_formatting():
    vocal = COMMUNITY_GOAL_METRICS_BY_KEY["vocal"]
    assert vocal.to_base_value(25) == 25 * 3600
    assert format_goal_value("vocal", 25 * 3600 + 30 * 60) == "25h 30m"

    xp = COMMUNITY_GOAL_METRICS_BY_KEY["xp"]
    assert xp.to_base_value(5000) == 5000
    assert format_goal_value("xp", 5000) == "5000 XP"


def test_aggregate_metric_total_across_seasons_and_users():
    seasons = [
        {
            "users": {
                "1": {"messages": 10, "voice_seconds": 120},
                "2": {"messages": 7, "voice_seconds": 60},
            }
        },
        {
            "users": {
                "1": {"messages": 5, "voice_seconds": 30},
                "3": {"messages": 8},
            }
        },
    ]

    assert aggregate_metric_total(seasons, "messages") == 30
    assert aggregate_metric_total(seasons, "voice_seconds") == 210


def test_goal_progress_uses_creation_baseline_only():
    assert goal_progress(1250, 1000, 500) == 250
    assert goal_progress(900, 1000, 500) == 0
    assert progress_percent(250, 500) == 50
    assert progress_percent(700, 500) == 100
    assert progress_bar(250, 500) == "█████░░░░░"


@pytest.mark.asyncio
async def test_store_rejects_second_active_goal_for_same_metric(tmp_path):
    store = CommunityGoalStore(tmp_path / "goals.json")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)

    first = await store.create_goal(
        metric_key="messages",
        target=1000,
        baseline_total=500,
        created_by=42,
        created_at=now,
        ends_at=now + timedelta(days=7),
        title="Semaine active",
    )
    assert first["status"] == "active"

    with pytest.raises(ValueError, match="already exists"):
        await store.create_goal(
            metric_key="messages",
            target=2000,
            baseline_total=500,
            created_by=42,
            created_at=now,
            ends_at=now + timedelta(days=14),
        )


@pytest.mark.asyncio
async def test_finished_goal_allows_new_goal_same_metric(tmp_path):
    path = tmp_path / "goals.json"
    store = CommunityGoalStore(path)
    now = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)

    first = await store.create_goal(
        metric_key="xp",
        target=5000,
        baseline_total=10000,
        created_by=7,
        created_at=now,
        ends_at=now + timedelta(days=10),
        reward_text="Badge collectif",
    )
    finished = await store.finish_goal(
        first["id"],
        status="completed",
        final_progress=5200,
        at=now + timedelta(days=2),
    )
    assert finished is not None
    assert finished["status"] == "completed"
    assert finished["final_progress"] == 5200
    assert finished["completed_at"] is not None

    second = await store.create_goal(
        metric_key="xp",
        target=8000,
        baseline_total=15200,
        created_by=7,
        created_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=20),
    )
    assert second["status"] == "active"
    assert second["id"] != first["id"]

    reloaded = CommunityGoalStore(path)
    goals = await reloaded.list_goals()
    assert {goal["status"] for goal in goals} == {"active", "completed"}


@pytest.mark.asyncio
async def test_cancelled_goal_is_persisted(tmp_path):
    path = tmp_path / "goals.json"
    store = CommunityGoalStore(path)
    now = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)

    goal = await store.create_goal(
        metric_key="casino",
        target=100,
        baseline_total=40,
        created_by=99,
        created_at=now,
        ends_at=now + timedelta(days=5),
    )
    cancelled = await store.finish_goal(
        goal["id"],
        status="cancelled",
        final_progress=12,
        at=now + timedelta(hours=1),
    )
    assert cancelled is not None
    assert cancelled["cancelled_at"] is not None
    assert cancelled["final_progress"] == 12

    reloaded = CommunityGoalStore(path)
    active = await reloaded.list_goals(status="active")
    assert active == []
    cancelled_goals = await reloaded.list_goals(status="cancelled")
    assert len(cancelled_goals) == 1
