from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storage.community_goal_store import CommunityGoalStore


@pytest.mark.asyncio
async def test_automatic_goal_metadata_is_persisted(tmp_path: Path):
    path = tmp_path / "goals.json"
    store = CommunityGoalStore(path)
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    created = await store.create_goal(
        metric_key="messages",
        target=1600,
        baseline_total=5000,
        created_by=0,
        created_at=now,
        ends_at=now + timedelta(days=2),
        title="Le Refuge s’anime",
        source="automatic",
        metadata={"difficulty": "easy", "duration_days": 2},
        require_no_active=True,
    )

    assert created["source"] == "automatic"
    assert created["metadata"] == {"difficulty": "easy", "duration_days": 2}

    reloaded = CommunityGoalStore(path)
    goals = await reloaded.list_goals(status="active")
    assert len(goals) == 1
    assert goals[0]["source"] == "automatic"
    assert goals[0]["metadata"]["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_require_no_active_rejects_even_a_different_metric(tmp_path: Path):
    store = CommunityGoalStore(tmp_path / "goals.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await store.create_goal(
        metric_key="xp",
        target=1000,
        baseline_total=0,
        created_by=42,
        created_at=now,
        ends_at=now + timedelta(days=2),
    )

    with pytest.raises(ValueError, match="active goal"):
        await store.create_goal(
            metric_key="messages",
            target=1000,
            baseline_total=0,
            created_by=0,
            created_at=now,
            ends_at=now + timedelta(days=2),
            source="automatic",
            require_no_active=True,
        )


@pytest.mark.asyncio
async def test_legacy_goal_defaults_to_manual_source_without_breaking_api(tmp_path: Path):
    store = CommunityGoalStore(tmp_path / "goals.json")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    goal = await store.create_goal(
        metric_key="casino",
        target=100,
        baseline_total=20,
        created_by=7,
        created_at=now,
        ends_at=now + timedelta(days=3),
    )

    assert goal["source"] == "manual"
    assert goal["metadata"] == {}
