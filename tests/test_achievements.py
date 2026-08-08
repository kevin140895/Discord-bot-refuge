import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cogs.achievements import _member_metrics
from storage.achievement_store import AchievementStore
from utils.achievements import ACHIEVEMENTS, qualifying_achievement_ids


def test_initial_catalog_has_nine_unique_achievements() -> None:
    assert len(ACHIEVEMENTS) == 9
    assert len({achievement.id for achievement in ACHIEVEMENTS}) == 9


def test_qualification_unlocks_reached_thresholds_only() -> None:
    qualified = qualifying_achievement_ids(
        {
            "level": 10,
            "casino_bets": 12,
            "tenure_days": 40,
        }
    )

    assert qualified == [
        "level_5",
        "level_10",
        "casino_1_bet",
        "casino_10_bets",
        "tenure_30_days",
    ]


def test_member_metrics_reuse_existing_authoritative_sources() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    member = SimpleNamespace(
        id=42,
        joined_at=now - timedelta(days=200, hours=3),
    )

    metrics = _member_metrics(
        member,
        {"42": {"level": 12, "xp": 99999}},
        {"42": {"bets": 15, "wagered": 1000, "winnings": 800}},
        now=now,
    )

    assert metrics == {
        "level": 12,
        "casino_bets": 15,
        "tenure_days": 200,
    }


@pytest.mark.asyncio
async def test_unlock_many_is_idempotent_and_persistent(tmp_path) -> None:
    path = tmp_path / "achievements.json"
    unlocked_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    store = AchievementStore(path)

    first = await store.unlock_many(
        42,
        ["level_5", "level_10", "level_5"],
        unlocked_at=unlocked_at,
    )
    second = await store.unlock_many(
        42,
        ["level_5", "level_10"],
        unlocked_at=unlocked_at + timedelta(days=1),
    )

    assert first == ["level_5", "level_10"]
    assert second == []

    reloaded = AchievementStore(path)
    assert await reloaded.get_user_achievements(42) == {
        "level_5": unlocked_at.isoformat(),
        "level_10": unlocked_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_unlock_batch_persists_multiple_members_in_one_snapshot(tmp_path) -> None:
    path = tmp_path / "achievements.json"
    unlocked_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    store = AchievementStore(path)

    result = await store.unlock_batch(
        {
            1: ["level_5", "tenure_30_days"],
            2: ["casino_1_bet"],
        },
        unlocked_at=unlocked_at,
    )

    assert result == {
        1: ["level_5", "tenure_30_days"],
        2: ["casino_1_bet"],
    }

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["users"]["1"] == {
        "level_5": unlocked_at.isoformat(),
        "tenure_30_days": unlocked_at.isoformat(),
    }
    assert payload["users"]["2"] == {
        "casino_1_bet": unlocked_at.isoformat(),
    }
