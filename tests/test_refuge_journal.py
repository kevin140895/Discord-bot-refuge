from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.refuge_journal import RefugeJournalService, publication_key_for
from storage.refuge_journal_store import RefugeJournalStore


class FakeSeasonStore:
    def __init__(self, seasons: dict[str, dict]) -> None:
        self.seasons = seasons

    async def list_seasons(self) -> list[str]:
        return sorted(self.seasons, reverse=True)

    async def get_season(self, season_id: str):
        return self.seasons.get(season_id)


class FakeAchievementStore:
    def __init__(self, snapshot: dict | None = None) -> None:
        self.snapshot = snapshot or {"users": {}}

    async def get_snapshot(self) -> dict:
        return self.snapshot


class FakeWorldStore:
    def __init__(self, events=()) -> None:
        self.events = events

    async def get_state(self):
        return SimpleNamespace(events=self.events)


def _user(**values: int) -> dict[str, int]:
    defaults = {
        "xp_earned": 0,
        "messages": 0,
        "voice_seconds": 0,
        "casino_bets": 0,
        "casino_net": 0,
    }
    defaults.update(values)
    return defaults


@pytest.mark.asyncio
async def test_first_start_creates_baseline_without_issue(tmp_path: Path) -> None:
    seasons = FakeSeasonStore(
        {"2026-08": {"users": {"10": _user(xp_earned=100, messages=8)}}}
    )
    store = RefugeJournalStore(tmp_path / "journal.json")
    service = RefugeJournalService(
        journal_store=store,
        seasonal_store=seasons,
        achievements_store=FakeAchievementStore(),
        world_store=FakeWorldStore(),
        game_events={},
    )
    at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)

    issue = await service.build_issue(at=at, guild_id=1)

    assert issue is None
    state = await store.get_state()
    assert state["baseline"]["users"]["10"]["xp_earned"] == 100
    assert state["last_issue_number"] == 0


@pytest.mark.asyncio
async def test_weekly_delta_crosses_month_boundary_without_reset(tmp_path: Path) -> None:
    seasons = FakeSeasonStore(
        {"2026-07": {"users": {"10": _user(xp_earned=1000, messages=100)}}}
    )
    store = RefugeJournalStore(tmp_path / "journal.json")
    service = RefugeJournalService(
        journal_store=store,
        seasonal_store=seasons,
        achievements_store=FakeAchievementStore(),
        world_store=FakeWorldStore(),
        game_events={},
    )
    start = datetime(2026, 7, 31, 18, tzinfo=timezone.utc)
    assert await service.ensure_baseline(at=start) is True

    seasons.seasons["2026-08"] = {
        "users": {
            "10": _user(
                xp_earned=250,
                messages=30,
                voice_seconds=7200,
                casino_bets=4,
                casino_net=-40,
            ),
            "20": _user(xp_earned=500, messages=12, voice_seconds=3600),
        }
    }
    issue = await service.build_issue(
        at=datetime(2026, 8, 2, 18, tzinfo=timezone.utc),
        guild_id=1,
    )

    assert issue is not None
    assert issue.total_xp == 750
    assert issue.total_messages == 42
    assert issue.total_voice_seconds == 10800
    assert issue.casino_bets == 4
    assert issue.casino_net == -40
    assert issue.xp_leader is not None and issue.xp_leader.user_id == 20
    assert issue.messages_leader is not None and issue.messages_leader.user_id == 10


@pytest.mark.asyncio
async def test_timestamped_sources_are_filtered_to_baseline_period(tmp_path: Path) -> None:
    start = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    inside = start + timedelta(days=2)
    before = start - timedelta(days=1)
    seasons = FakeSeasonStore({"2026-08": {"users": {}}})
    achievements = FakeAchievementStore(
        {
            "users": {
                "10": {
                    "level_5": inside.isoformat(),
                    "level_10": before.isoformat(),
                }
            }
        }
    )
    world_events = (
        SimpleNamespace(
            occurred_at=inside.isoformat(),
            event_type="construction_completed",
            data={"project_name": "Tour du Refuge"},
        ),
        SimpleNamespace(
            occurred_at=before.isoformat(),
            event_type="building_level_reached",
            data={},
        ),
    )
    game_events = {
        "inside": SimpleNamespace(
            guild_id=1,
            ended_at=inside,
            state="finished",
            participants={10, 20},
            game_name="Rocket League",
            game_type="multi",
        ),
        "outside": SimpleNamespace(
            guild_id=1,
            ended_at=before,
            state="finished",
            participants={10},
            game_name="Valorant",
            game_type="multi",
        ),
    }
    store = RefugeJournalStore(tmp_path / "journal.json")
    service = RefugeJournalService(
        journal_store=store,
        seasonal_store=seasons,
        achievements_store=achievements,
        world_store=FakeWorldStore(world_events),
        game_events=game_events,
    )
    assert await service.ensure_baseline(at=start) is True

    issue = await service.build_issue(at=start + timedelta(days=6), guild_id=1)

    assert issue is not None
    assert issue.achievement_count == 1
    assert issue.achievement_highlights[0].achievement_id == "level_5"
    assert issue.game_event_count == 1
    assert issue.game_participations == 2
    assert issue.game_names == ("Rocket League",)
    assert issue.refuge_event_count == 1
    assert issue.refuge_event_labels == ("Tour du Refuge",)


@pytest.mark.asyncio
async def test_commit_publication_is_idempotency_ledger_and_advances_baseline(tmp_path: Path) -> None:
    store = RefugeJournalStore(tmp_path / "journal.json")
    start = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    end = datetime(2026, 8, 16, 18, tzinfo=timezone.utc)
    users = {"10": _user(xp_earned=250)}
    await store.ensure_baseline(captured_at=start, users={"10": _user()})

    await store.commit_publication(
        publication_key="2026-W33",
        issue_number=1,
        message_id=123,
        published_at=end,
        period_start=start,
        period_end=end,
        users=users,
    )

    assert await store.was_published("2026-W33") is True
    state = await store.get_state()
    assert state["last_issue_number"] == 1
    assert state["baseline"]["users"]["10"]["xp_earned"] == 250


def test_publication_key_uses_paris_iso_week() -> None:
    # 23:30 UTC Sunday is already Monday in Paris during summer time.
    at = datetime(2026, 8, 9, 23, 30, tzinfo=timezone.utc)
    assert publication_key_for(at) == "2026-W33"
