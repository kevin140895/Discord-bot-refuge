from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Mapping, Sequence

from models.refuge_world import (
    RefugeBuildingState,
    RefugeHistoricalEvent,
    RefugeWorldState,
)
from rendering.refuge_world import RefugeRenderContext
from services.refuge_casino import (
    CASINO_BUILDING_ID,
    CASINO_EVENTS,
    CASINO_SECRET_EVENTS,
    casino_is_open,
)
from services.refuge_fire import FIRE_BUILDING_ID, FIRE_SECRET_EVENTS
from services.refuge_hall import HALL_BUILDING_ID, HALL_SECRET_EVENTS
from services.refuge_timeline import RefugeTimelineService, refuge_timeline_service
from services.refuge_world_coordination import refuge_world_mutation_lock
from storage.achievement_store import AchievementStore, achievement_store
from storage.refuge_activity_store import RefugeActivityStore, refuge_activity_store
from storage.refuge_casino_activity_store import (
    RefugeCasinoActivityStore,
    refuge_casino_activity_store,
)
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from utils.achievements import ACHIEVEMENT_BY_ID
from utils.timezones import PARIS_TZ


REFUGE_SECRETS_STATE_KEY: Final[str] = "refuge_secrets"
REFUGE_SECRETS_VERSION: Final[int] = 1
RECENT_CASINO_WINDOW_SECONDS: Final[int] = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class RefugeSecretDiscovery:
    building_id: str
    marker_id: str
    name: str
    event_type: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class RefugeSecretsSyncResult:
    state: RefugeWorldState
    discoveries: tuple[RefugeSecretDiscovery, ...]
    changed: bool


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


def _iso(value: datetime) -> str:
    return _aware_utc(value).isoformat()


def _secrets_meta(state: RefugeWorldState) -> dict[str, Any]:
    raw = state.state.get(REFUGE_SECRETS_STATE_KEY, {})
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "version": REFUGE_SECRETS_VERSION,
        "enabled_at": str(source.get("enabled_at") or "") or None,
    }


def _with_meta(state: RefugeWorldState, meta: Mapping[str, Any]) -> RefugeWorldState:
    payload = dict(state.state)
    payload[REFUGE_SECRETS_STATE_KEY] = dict(meta)
    return replace(state, state=payload)


def _building(state: RefugeWorldState, building_id: str) -> RefugeBuildingState | None:
    return next(
        (item for item in state.buildings if item.building_id == building_id),
        None,
    )


def _replace_building(
    state: RefugeWorldState,
    building: RefugeBuildingState,
) -> RefugeWorldState:
    buildings = {item.building_id: item for item in state.buildings}
    buildings[building.building_id] = building
    return replace(
        state,
        buildings=tuple(sorted(buildings.values(), key=lambda item: item.building_id)),
    )


def _string_values(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in value if str(item).strip()}


def _voice_evidence(
    snapshot: Mapping[str, Any],
    *,
    enabled_at: datetime,
    now: datetime,
) -> tuple[tuple[datetime, str], ...]:
    raw = snapshot.get("recent_voice_buckets", {})
    if not isinstance(raw, Mapping):
        return ()
    evidence: list[tuple[datetime, str]] = []
    for raw_key, raw_seconds in raw.items():
        moment = _parse_timestamp(raw_key)
        if moment is None or moment < enabled_at or moment > now:
            continue
        try:
            seconds = int(raw_seconds)
        except (TypeError, ValueError):
            continue
        if seconds <= 0:
            continue
        local = moment.astimezone(PARIS_TZ)
        context = RefugeRenderContext.from_datetime(local)
        evidence.append((moment, context.daypart))
    evidence.sort(key=lambda item: item[0])
    return tuple(evidence)


def _achievement_evidence(
    snapshot: Mapping[str, Any],
    *,
    enabled_at: datetime,
    now: datetime,
) -> tuple[tuple[datetime, int, str], ...]:
    users = snapshot.get("users", {})
    if not isinstance(users, Mapping):
        return ()
    events: list[tuple[datetime, int, str]] = []
    for raw_user_id, raw_achievements in users.items():
        if not isinstance(raw_achievements, Mapping):
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        for raw_achievement_id, raw_unlocked_at in raw_achievements.items():
            moment = _parse_timestamp(raw_unlocked_at)
            if moment is None or moment < enabled_at or moment > now:
                continue
            achievement_id = str(raw_achievement_id)
            if achievement_id not in ACHIEVEMENT_BY_ID:
                continue
            events.append((moment, user_id, achievement_id))
    events.sort(key=lambda item: (item[0], item[2], item[1]))
    return tuple(events)


def _casino_evidence(
    snapshot: Mapping[str, Any],
    *,
    enabled_at: datetime,
    now: datetime,
) -> tuple[dict[str, int], tuple[dict[str, Any], ...], tuple[datetime, ...]]:
    cutoff = max(enabled_at, now - timedelta(seconds=RECENT_CASINO_WINDOW_SECONDS))
    totals = {
        "roulette_wagered_xp": 0,
        "roulette_payout_xp": 0,
        "machine_payout_xp": 0,
        "transactions": 0,
    }
    transaction_times: list[datetime] = []
    raw_buckets = snapshot.get("recent_buckets", {})
    if isinstance(raw_buckets, Mapping):
        for raw_key, raw_payload in raw_buckets.items():
            if not isinstance(raw_payload, Mapping):
                continue
            moment = _parse_timestamp(raw_key)
            if moment is None or moment < cutoff or moment > now:
                continue
            for field in totals:
                try:
                    totals[field] += max(0, int(raw_payload.get(field, 0)))
                except (TypeError, ValueError):
                    continue
            try:
                transactions = max(0, int(raw_payload.get("transactions", 0)))
            except (TypeError, ValueError):
                transactions = 0
            if transactions > 0:
                transaction_times.append(moment)

    jackpots: list[dict[str, Any]] = []
    raw_jackpots = snapshot.get("jackpots", [])
    if isinstance(raw_jackpots, list):
        for item in raw_jackpots:
            if not isinstance(item, Mapping):
                continue
            moment = _parse_timestamp(item.get("occurred_at"))
            if moment is None or moment < enabled_at or moment > now:
                continue
            candidate = dict(item)
            candidate["_moment"] = moment
            jackpots.append(candidate)
    jackpots.sort(
        key=lambda item: (
            item.get("_moment", datetime.min.replace(tzinfo=timezone.utc)),
            str(item.get("event_id", "")),
        )
    )
    transaction_times.sort()
    return totals, tuple(jackpots), tuple(transaction_times)


def _discovery_exists(state: RefugeWorldState, event_id: str) -> bool:
    return any(event.event_id == event_id for event in state.events)


def _discover(
    state: RefugeWorldState,
    *,
    building_id: str,
    marker_id: str,
    name: str,
    state_key: str,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    marker_data_key: str,
) -> tuple[RefugeWorldState, RefugeSecretDiscovery | None]:
    if _discovery_exists(state, event_id):
        return state, None
    building = _building(state, building_id)
    if building is None:
        return state, None

    values = _string_values(building.state.get(state_key, ()))
    values.add(marker_id)
    building_state = dict(building.state)
    building_state[state_key] = sorted(values)
    updated = _replace_building(state, replace(building, state=building_state))
    event = RefugeHistoricalEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=_iso(occurred_at),
        data={
            "building_id": building_id,
            marker_data_key: marker_id,
            "name": name,
        },
    )
    updated = replace(updated, events=updated.events + (event,))
    discovery = RefugeSecretDiscovery(
        building_id=building_id,
        marker_id=marker_id,
        name=name,
        event_type=event_type,
        occurred_at=event.occurred_at,
    )
    return updated, discovery


def _latest_time(values: Sequence[datetime], fallback: datetime) -> datetime:
    return max(values) if values else fallback


class RefugeSecretsService:
    """Discover hidden Refuge events from real, prospective community evidence."""

    def __init__(
        self,
        *,
        world_store: RefugeWorldStore = refuge_world_store,
        activity_store: RefugeActivityStore = refuge_activity_store,
        achievement_store_: AchievementStore = achievement_store,
        casino_activity_store: RefugeCasinoActivityStore = refuge_casino_activity_store,
        timeline_service: RefugeTimelineService = refuge_timeline_service,
    ) -> None:
        self.world_store = world_store
        self.activity_store = activity_store
        self.achievement_store = achievement_store_
        self.casino_activity_store = casino_activity_store
        self.timeline_service = timeline_service

    async def sync(self, *, at: datetime | None = None) -> RefugeSecretsSyncResult:
        now = _aware_utc(at)
        async with refuge_world_mutation_lock():
            await self.timeline_service.sync_under_world_lock(at=now)
            return await self.sync_under_world_lock(at=now)

    async def sync_under_world_lock(
        self,
        *,
        at: datetime | None = None,
    ) -> RefugeSecretsSyncResult:
        now = _aware_utc(at)
        state = await self.world_store.initialize(created_at=now)
        meta = _secrets_meta(state)
        enabled_at = _parse_timestamp(meta.get("enabled_at"))
        if enabled_at is None:
            meta["enabled_at"] = now.isoformat()
            saved = await self.world_store.save_state(_with_meta(state, meta))
            return RefugeSecretsSyncResult(saved, (), True)

        voice_snapshot = await self.activity_store.get_snapshot()
        achievement_snapshot = await self.achievement_store.get_snapshot()
        casino_snapshot = await self.casino_activity_store.get_snapshot(at=now)

        voice = _voice_evidence(
            voice_snapshot,
            enabled_at=enabled_at,
            now=now,
        )
        achievements = _achievement_evidence(
            achievement_snapshot,
            enabled_at=enabled_at,
            now=now,
        )
        casino_totals, jackpots, transaction_times = _casino_evidence(
            casino_snapshot,
            enabled_at=enabled_at,
            now=now,
        )

        discoveries: list[RefugeSecretDiscovery] = []

        # Fire secrets are based only on qualifying community voice buckets.
        if voice:
            state, discovery = _discover(
                state,
                building_id=FIRE_BUILDING_ID,
                marker_id="first_visitor",
                name=FIRE_SECRET_EVENTS["first_visitor"],
                state_key="secret_events",
                event_id="fire:secret:first_visitor",
                event_type="fire_secret_discovered",
                occurred_at=voice[0][0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        night_times = [moment for moment, daypart in voice if daypart == "night"]
        if night_times:
            state, discovery = _discover(
                state,
                building_id=FIRE_BUILDING_ID,
                marker_id="night_of_stars",
                name=FIRE_SECRET_EVENTS["night_of_stars"],
                state_key="secret_events",
                event_id="fire:secret:night_of_stars",
                event_type="fire_secret_discovered",
                occurred_at=night_times[0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        voice_dayparts = {daypart for _moment, daypart in voice}
        if {"morning", "day", "sunset", "night"}.issubset(voice_dayparts):
            state, discovery = _discover(
                state,
                building_id=FIRE_BUILDING_ID,
                marker_id="full_circle",
                name=FIRE_SECRET_EVENTS["full_circle"],
                state_key="secret_events",
                event_id="fire:secret:full_circle",
                event_type="fire_secret_discovered",
                occurred_at=voice[-1][0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        # Hall secrets use real achievement unlocks and the existing catalogue.
        if achievements:
            state, discovery = _discover(
                state,
                building_id=HALL_BUILDING_ID,
                marker_id="memory_flame",
                name=HALL_SECRET_EVENTS["memory_flame"],
                state_key="secret_events",
                event_id="hall:secret:memory_flame",
                event_type="hall_secret_discovered",
                occurred_at=achievements[0][0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        known_categories = {
            definition.category for definition in ACHIEVEMENT_BY_ID.values()
        }
        observed_categories = {
            ACHIEVEMENT_BY_ID[achievement_id].category
            for _moment, _user_id, achievement_id in achievements
            if achievement_id in ACHIEVEMENT_BY_ID
        }
        if known_categories and known_categories.issubset(observed_categories):
            state, discovery = _discover(
                state,
                building_id=HALL_BUILDING_ID,
                marker_id="endless_book",
                name=HALL_SECRET_EVENTS["endless_book"],
                state_key="secret_events",
                event_id="hall:secret:endless_book",
                event_type="hall_secret_discovered",
                occurred_at=achievements[-1][0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        achievement_counts: dict[str, int] = {}
        achievers = {user_id for _moment, user_id, _achievement_id in achievements}
        for _moment, _user_id, achievement_id in achievements:
            achievement_counts[achievement_id] = achievement_counts.get(achievement_id, 0) + 1
        if len(achievers) >= 2 and any(count == 1 for count in achievement_counts.values()):
            state, discovery = _discover(
                state,
                building_id=HALL_BUILDING_ID,
                marker_id="forgotten_crown",
                name=HALL_SECRET_EVENTS["forgotten_crown"],
                state_key="secret_events",
                event_id="hall:secret:forgotten_crown",
                event_type="hall_secret_discovered",
                occurred_at=achievements[-1][0],
                marker_data_key="secret_id",
            )
            if discovery:
                discoveries.append(discovery)

        # Casino regular events and secrets use only observed prospective flows.
        wagered = int(casino_totals["roulette_wagered_xp"])
        paid = int(casino_totals["roulette_payout_xp"]) + int(
            casino_totals["machine_payout_xp"]
        )
        transactions = int(casino_totals["transactions"])
        casino_occurrence = _latest_time(transaction_times, now)

        if transactions > 0 and wagered > 0 and paid > wagered:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="grand_heist",
                name=CASINO_EVENTS["grand_heist"],
                state_key="casino_events",
                event_id="casino:casino_events:grand_heist",
                event_type="casino_event_discovered",
                occurred_at=casino_occurrence,
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        if transactions > 0 and any(
            RefugeRenderContext.from_datetime(moment.astimezone(PARIS_TZ)).daypart == "night"
            for moment in transaction_times
        ):
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="black_night",
                name=CASINO_EVENTS["black_night"],
                state_key="casino_events",
                event_id="casino:casino_events:black_night",
                event_type="casino_event_discovered",
                occurred_at=casino_occurrence,
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        jackpot_500 = [item for item in jackpots if int(item.get("tier", 0) or 0) == 500]
        if jackpot_500:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="break_in",
                name=CASINO_EVENTS["break_in"],
                state_key="casino_events",
                event_id="casino:casino_events:break_in",
                event_type="casino_event_discovered",
                occurred_at=jackpot_500[0]["_moment"],
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        if transactions > 0 and wagered > 0 and paid > 0 and wagered > paid:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="house_always_wins",
                name=CASINO_EVENTS["house_always_wins"],
                state_key="casino_events",
                event_id="casino:casino_events:house_always_wins",
                event_type="casino_event_discovered",
                occurred_at=casino_occurrence,
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        if transactions > 0 and wagered > 0 and paid > 0 and wagered == paid:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="black_cat",
                name=CASINO_SECRET_EVENTS["black_cat"],
                state_key="secret_events",
                event_id="casino:secret_events:black_cat",
                event_type="casino_secret_discovered",
                occurred_at=casino_occurrence,
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        jackpot_1000 = [item for item in jackpots if int(item.get("tier", 0) or 0) == 1000]
        if jackpot_1000:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="diamond",
                name=CASINO_SECRET_EVENTS["diamond"],
                state_key="secret_events",
                event_id="casino:secret_events:diamond",
                event_type="casino_secret_discovered",
                occurred_at=jackpot_1000[0]["_moment"],
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        ghost_jackpots = [
            item
            for item in jackpots
            if not casino_is_open(item["_moment"])
        ]
        if ghost_jackpots:
            state, discovery = _discover(
                state,
                building_id=CASINO_BUILDING_ID,
                marker_id="ghost_player",
                name=CASINO_SECRET_EVENTS["ghost_player"],
                state_key="secret_events",
                event_id="casino:secret_events:ghost_player",
                event_type="casino_secret_discovered",
                occurred_at=ghost_jackpots[0]["_moment"],
                marker_data_key="marker_id",
            )
            if discovery:
                discoveries.append(discovery)

        if not discoveries:
            return RefugeSecretsSyncResult(state, (), False)

        saved = await self.world_store.save_state(state)
        return RefugeSecretsSyncResult(saved, tuple(discoveries), True)


refuge_secrets_service = RefugeSecretsService()


__all__ = [
    "REFUGE_SECRETS_STATE_KEY",
    "REFUGE_SECRETS_VERSION",
    "RefugeSecretDiscovery",
    "RefugeSecretsService",
    "RefugeSecretsSyncResult",
    "refuge_secrets_service",
]
