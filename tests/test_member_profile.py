from __future__ import annotations

import json

import pytest

from services.member_profile import (
    MemberProfileService,
    build_member_profile_snapshot,
)


def test_build_member_profile_snapshot_combines_existing_sources() -> None:
    snapshot = build_member_profile_snapshot(
        user_id=42,
        season_id="2026-08",
        xp_payload={"xp": 12_450, "level": 18},
        unlocked_achievements={
            "level_5": "2026-08-01T00:00:00+00:00",
            "level_10": "2026-08-02T00:00:00+00:00",
            "legacy_badge": "2025-01-01T00:00:00+00:00",
        },
        season_payload={
            "users": {
                "7": {
                    "xp_earned": 5_000,
                    "messages": 100,
                    "voice_seconds": 100,
                    "casino_bets": 2,
                    "casino_net": 900,
                },
                "42": {
                    "xp_earned": 4_000,
                    "messages": 300,
                    "voice_seconds": 7_200,
                    "casino_bets": 3,
                    "casino_net": -200,
                },
                "99": {
                    "xp_earned": 3_000,
                    "messages": 200,
                    "voice_seconds": 3_600,
                    "casino_bets": 1,
                    "casino_net": -500,
                },
            }
        },
        casino_payload={"bets": 48, "wagered": 8_000, "winnings": 9_320},
    )

    assert snapshot.user_id == 42
    assert snapshot.xp == 12_450
    assert snapshot.level == 18
    assert snapshot.achievements_unlocked == 2
    assert snapshot.achievements_total == 9
    assert snapshot.achievement_ids == ("level_10", "level_5")

    assert snapshot.season_xp == 4_000
    assert snapshot.season_xp_rank == 2
    assert snapshot.season_messages == 300
    assert snapshot.season_messages_rank == 1
    assert snapshot.season_voice_seconds == 7_200
    assert snapshot.season_voice_rank == 1
    assert snapshot.season_casino_net == -200
    assert snapshot.season_casino_rank == 2

    assert snapshot.casino_bets == 48
    assert snapshot.casino_wagered == 8_000
    assert snapshot.casino_winnings == 9_320
    assert snapshot.casino_net == 1_320


def test_build_member_profile_snapshot_handles_missing_and_invalid_values() -> None:
    snapshot = build_member_profile_snapshot(
        user_id=42,
        season_id="2026-08",
        xp_payload={"xp": "invalid", "level": -2},
        unlocked_achievements={},
        season_payload=None,
        casino_payload={"bets": -4, "wagered": "bad", "winnings": None},
    )

    assert snapshot.xp == 0
    assert snapshot.level == 0
    assert snapshot.achievements_unlocked == 0
    assert snapshot.season_xp == 0
    assert snapshot.season_xp_rank is None
    assert snapshot.season_messages_rank is None
    assert snapshot.season_voice_rank is None
    assert snapshot.season_casino_rank is None
    assert snapshot.casino_bets == 0
    assert snapshot.casino_net == 0


def test_build_member_profile_snapshot_validates_season_id() -> None:
    with pytest.raises(ValueError):
        build_member_profile_snapshot(
            user_id=42,
            season_id="august",
            xp_payload={},
            unlocked_achievements={},
            season_payload=None,
            casino_payload={},
        )


class _FakeXPReader:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def get_user_data(self, user_id: int) -> dict[str, int]:
        self.calls.append(user_id)
        return {"xp": 900, "level": 4}


class _FakeAchievementReader:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def get_user_achievements(self, user_id: int) -> dict[str, str]:
        self.calls.append(user_id)
        return {"casino_1_bet": "2026-08-01T00:00:00+00:00"}


class _FakeSeasonReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_season(self, season_id: str) -> dict[str, object]:
        self.calls.append(season_id)
        return {
            "users": {
                "42": {
                    "xp_earned": 100,
                    "messages": 5,
                    "voice_seconds": 60,
                    "casino_bets": 1,
                    "casino_net": 20,
                }
            }
        }


@pytest.mark.asyncio
async def test_service_reads_authoritative_sources_without_creating_storage(tmp_path) -> None:
    casino_path = tmp_path / "pari_xp_state.json"
    original = {
        "players": {
            "42": {
                "bets": 3,
                "wagered": 150,
                "winnings": 220,
            }
        }
    }
    casino_path.write_text(json.dumps(original), encoding="utf-8")

    xp_reader = _FakeXPReader()
    achievement_reader = _FakeAchievementReader()
    season_reader = _FakeSeasonReader()
    service = MemberProfileService(
        xp_reader=xp_reader,
        achievement_reader=achievement_reader,
        season_reader=season_reader,
        casino_state_file=casino_path,
    )

    snapshot = await service.get_snapshot(42, season_id="2026-08")

    assert xp_reader.calls == [42]
    assert achievement_reader.calls == [42]
    assert season_reader.calls == ["2026-08"]
    assert snapshot.xp == 900
    assert snapshot.season_messages == 5
    assert snapshot.casino_net == 70
    assert json.loads(casino_path.read_text(encoding="utf-8")) == original
