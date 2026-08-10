from datetime import datetime, timedelta, timezone

import pytest

import cogs.xp as xp
import utils.voice_bonus as voice_bonus


@pytest.fixture(autouse=True)
def reset_temporal_boost_state():
    voice_bonus.set_voice_bonus(False)
    voice_bonus.VOICE_BONUS_WINDOWS.clear()
    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()
    yield
    voice_bonus.set_voice_bonus(False)
    voice_bonus.VOICE_BONUS_WINDOWS.clear()
    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=timezone.utc)


def test_global_boost_started_near_leave_only_doubles_overlap():
    # 18:00 -> 22:35 = 275 completed minutes = 825 XP base.
    # Only 22:30 -> 22:35 is under Double XP: +15 XP, total 840.
    voice_bonus.set_voice_bonus(True, at=_dt(22, 30))
    voice_bonus.set_voice_bonus(False, at=_dt(23, 30))

    assert xp.calculate_voice_xp(1, _dt(18), _dt(22, 35)) == 840


def test_global_boost_that_expired_before_leave_is_still_counted():
    voice_bonus.set_voice_bonus(True, at=_dt(19))
    voice_bonus.set_voice_bonus(False, at=_dt(20))

    # 18:00 -> 22:00 = 720 XP base + 180 XP for the boosted hour.
    assert xp.calculate_voice_xp(1, _dt(18), _dt(22)) == 900


def test_personal_boost_started_near_leave_only_doubles_overlap():
    uid = 42
    xp.XP_BOOST_STARTS[str(uid)] = _dt(22, 30)
    xp.XP_BOOSTS[str(uid)] = _dt(23, 30)

    assert xp.calculate_voice_xp(uid, _dt(18), _dt(22, 35)) == 840


def test_expired_personal_boost_is_counted_for_its_real_window():
    uid = 43
    xp.XP_BOOST_STARTS[str(uid)] = _dt(19)
    xp.XP_BOOSTS[str(uid)] = _dt(20)

    assert xp.calculate_voice_xp(uid, _dt(18), _dt(22)) == 900


def test_global_and_personal_boosts_keep_existing_stacking_semantics():
    uid = 44
    start = _dt(18)
    end = _dt(19)
    global_start = start + timedelta(minutes=20)
    global_end = start + timedelta(minutes=40)
    personal_start = start + timedelta(minutes=30)
    personal_end = start + timedelta(minutes=50)

    voice_bonus.set_voice_bonus(True, at=global_start)
    voice_bonus.set_voice_bonus(False, at=global_end)
    xp.XP_BOOST_STARTS[str(uid)] = personal_start
    xp.XP_BOOSTS[str(uid)] = personal_end

    # 0-20: x1, 20-30: x2 global, 30-40: x4 global+personal,
    # 40-50: x2 personal, 50-60: x1 => 330 XP.
    assert xp.calculate_voice_xp(uid, start, end) == 330


def test_completed_minute_rounding_is_preserved_without_boost():
    start = _dt(18)
    end = start + timedelta(seconds=119)

    assert xp.calculate_voice_xp(1, start, end) == 3


def test_event_multiplier_remains_compatible_with_global_cap():
    start = _dt(18)
    end = _dt(19)
    voice_bonus.set_voice_bonus(True, at=start + timedelta(minutes=15))
    voice_bonus.set_voice_bonus(False, at=start + timedelta(minutes=45))

    # Event x3 is already above the global x2 cap for the whole hour.
    assert xp.calculate_voice_xp(1, start, end, event_multiplier=3.0) == 540


@pytest.mark.asyncio
async def test_voice_award_can_bypass_instant_personal_double_xp():
    xp.xp_store.data.clear()
    uid = 45
    xp.XP_BOOST_STARTS[str(uid)] = _dt(18)
    xp.XP_BOOSTS[str(uid)] = datetime.now(timezone.utc) + timedelta(hours=1)

    old, new, old_xp, total = await xp.award_xp(
        uid,
        10,
        guild_id=0,
        source="voice_leave",
        apply_personal_boost=False,
    )

    assert total == 10
