from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from storage.achievement_store import achievement_store
from storage.refuge_journal_store import RefugeJournalStore, refuge_journal_store
from storage.refuge_world_store import refuge_world_store
from storage.season_store import season_store
from utils.achievements import ACHIEVEMENT_BY_ID
from utils.game_events import EVENTS
from utils.timezones import PARIS_TZ


logger = logging.getLogger(__name__)

JOURNAL_FIELDS = (
    "xp_earned",
    "messages",
    "voice_seconds",
    "casino_bets",
    "casino_net",
)


@dataclass(frozen=True, slots=True)
class JournalLeader:
    user_id: int
    value: int


@dataclass(frozen=True, slots=True)
class JournalAchievement:
    user_id: int
    achievement_id: str
    emoji: str
    name: str


@dataclass(frozen=True, slots=True)
class RefugeJournalIssue:
    publication_key: str
    issue_number: int
    period_start: datetime
    period_end: datetime
    users_snapshot: dict[str, dict[str, int]]
    total_xp: int
    total_messages: int
    total_voice_seconds: int
    casino_bets: int
    casino_net: int
    xp_leader: JournalLeader | None
    messages_leader: JournalLeader | None
    voice_leader: JournalLeader | None
    achievement_count: int
    achievement_highlights: tuple[JournalAchievement, ...]
    game_event_count: int
    game_participations: int
    game_names: tuple[str, ...]
    refuge_event_count: int
    refuge_event_labels: tuple[str, ...]


def _aware_utc(value: datetime | None = None) -> datetime:
    moment = value or datetime.now(timezone.utc)
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


def publication_key_for(at: datetime | None = None) -> str:
    local = _aware_utc(at).astimezone(PARIS_TZ)
    iso = local.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _event_label(event_type: str, data: Mapping[str, Any]) -> str:
    explicit = data.get("name") or data.get("project_name")
    if explicit:
        return str(explicit)
    labels = {
        "building_level_reached": "Un bâtiment du Refuge a évolué",
        "casino_jackpot_observed": "Un jackpot a marqué le Casino",
        "hall_gallery_marker": "Une nouvelle trace est entrée au Hall",
        "construction_vote_opened": "Un vote de chantier s’est ouvert",
        "construction_vote_tied": "Un chantier a dû être départagé",
        "construction_started": "Une construction a commencé",
        "construction_completed": "Un monument a été inauguré",
    }
    if event_type.endswith("secret_discovered"):
        return "Un mystère du Refuge a été découvert"
    return labels.get(event_type, "Un événement a marqué le Refuge")


def _leader(
    deltas: Mapping[str, Mapping[str, int]],
    field: str,
) -> JournalLeader | None:
    candidates: list[tuple[int, int]] = []
    for raw_user_id, payload in deltas.items():
        try:
            user_id = int(raw_user_id)
            value = int(payload.get(field, 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            candidates.append((value, user_id))
    if not candidates:
        return None
    value, user_id = max(candidates, key=lambda row: (row[0], row[1]))
    return JournalLeader(user_id=user_id, value=value)


class RefugeJournalService:
    """Read existing bot data and build deterministic weekly Journal issues."""

    def __init__(
        self,
        *,
        journal_store: RefugeJournalStore = refuge_journal_store,
        seasonal_store=season_store,
        achievements_store=achievement_store,
        world_store=refuge_world_store,
        game_events: Mapping[str, Any] = EVENTS,
    ) -> None:
        self.journal_store = journal_store
        self.seasonal_store = seasonal_store
        self.achievements_store = achievements_store
        self.world_store = world_store
        self.game_events = game_events

    async def capture_users_snapshot(self) -> dict[str, dict[str, int]]:
        totals: dict[str, dict[str, int]] = {}
        for season_id in await self.seasonal_store.list_seasons():
            payload = await self.seasonal_store.get_season(season_id)
            if not isinstance(payload, Mapping):
                continue
            users = payload.get("users", {})
            if not isinstance(users, Mapping):
                continue
            for raw_user_id, raw_values in users.items():
                if not isinstance(raw_values, Mapping):
                    continue
                target = totals.setdefault(
                    str(raw_user_id),
                    {field: 0 for field in JOURNAL_FIELDS},
                )
                for field in JOURNAL_FIELDS:
                    try:
                        target[field] += int(raw_values.get(field, 0) or 0)
                    except (TypeError, ValueError):
                        continue
        return totals

    async def ensure_baseline(self, *, at: datetime | None = None) -> bool:
        now = _aware_utc(at)
        users = await self.capture_users_snapshot()
        return await self.journal_store.ensure_baseline(captured_at=now, users=users)

    @staticmethod
    def _deltas(
        current: Mapping[str, Mapping[str, int]],
        baseline: Mapping[str, Mapping[str, int]],
    ) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        user_ids = set(current) | set(baseline)
        for user_id in user_ids:
            current_payload = current.get(user_id, {})
            previous_payload = baseline.get(user_id, {})
            row: dict[str, int] = {}
            for field in JOURNAL_FIELDS:
                try:
                    now_value = int(current_payload.get(field, 0) or 0)
                    previous_value = int(previous_payload.get(field, 0) or 0)
                except (TypeError, ValueError):
                    now_value = previous_value = 0
                delta = now_value - previous_value
                # All fields except casino net are monotonic cumulative counters.
                if field != "casino_net":
                    delta = max(0, delta)
                row[field] = delta
            if any(value != 0 for value in row.values()):
                result[str(user_id)] = row
        return result

    async def _achievement_summary(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[int, tuple[JournalAchievement, ...]]:
        try:
            snapshot = await self.achievements_store.get_snapshot()
        except Exception:
            logger.exception("[Journal] lecture des succès impossible")
            return 0, ()
        users = snapshot.get("users", {}) if isinstance(snapshot, Mapping) else {}
        if not isinstance(users, Mapping):
            return 0, ()

        rows: list[tuple[datetime, JournalAchievement]] = []
        for raw_user_id, achievements in users.items():
            if not isinstance(achievements, Mapping):
                continue
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            for achievement_id, unlocked_at in achievements.items():
                moment = _parse_timestamp(unlocked_at)
                if moment is None or not start < moment <= end:
                    continue
                definition = ACHIEVEMENT_BY_ID.get(str(achievement_id))
                if definition is None:
                    continue
                rows.append(
                    (
                        moment,
                        JournalAchievement(
                            user_id=user_id,
                            achievement_id=definition.id,
                            emoji=definition.emoji,
                            name=definition.name,
                        ),
                    )
                )
        rows.sort(key=lambda row: row[0], reverse=True)
        return len(rows), tuple(item for _moment, item in rows[:4])

    def _game_summary(
        self,
        start: datetime,
        end: datetime,
        *,
        guild_id: int | None,
    ) -> tuple[int, int, tuple[str, ...]]:
        selected: list[Any] = []
        for event in self.game_events.values():
            if guild_id is not None and int(getattr(event, "guild_id", 0) or 0) != guild_id:
                continue
            ended_at = getattr(event, "ended_at", None)
            if not isinstance(ended_at, datetime):
                continue
            ended = _aware_utc(ended_at)
            if getattr(event, "state", "") != "finished" or not start < ended <= end:
                continue
            selected.append(event)

        names: list[str] = []
        participations = 0
        for event in sorted(selected, key=lambda item: _aware_utc(item.ended_at), reverse=True):
            participants = getattr(event, "participants", set())
            try:
                participations += len(participants)
            except TypeError:
                pass
            name = str(getattr(event, "game_name", "") or getattr(event, "game_type", "")).strip()
            if name and name not in names:
                names.append(name)
        return len(selected), participations, tuple(names[:4])

    async def _refuge_summary(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[int, tuple[str, ...]]:
        try:
            world = await self.world_store.get_state()
        except Exception:
            logger.exception("[Journal] lecture de la Chronologie impossible")
            return 0, ()

        rows: list[tuple[datetime, str]] = []
        for event in world.events:
            moment = _parse_timestamp(event.occurred_at)
            if moment is None or not start < moment <= end:
                continue
            rows.append((moment, _event_label(event.event_type, event.data)))
        rows.sort(key=lambda row: row[0], reverse=True)
        return len(rows), tuple(label for _moment, label in rows[:5])

    async def build_issue(
        self,
        *,
        at: datetime | None = None,
        guild_id: int | None = None,
    ) -> RefugeJournalIssue | None:
        now = _aware_utc(at)
        state = await self.journal_store.get_state()
        baseline = state.get("baseline")
        if not isinstance(baseline, Mapping):
            await self.ensure_baseline(at=now)
            return None

        period_start = _parse_timestamp(baseline.get("captured_at"))
        baseline_users = baseline.get("users", {})
        if period_start is None or not isinstance(baseline_users, Mapping):
            return None

        current = await self.capture_users_snapshot()
        deltas = self._deltas(current, baseline_users)

        def total(field: str) -> int:
            return sum(int(payload.get(field, 0) or 0) for payload in deltas.values())

        achievement_count, achievement_highlights = await self._achievement_summary(
            period_start, now
        )
        game_event_count, game_participations, game_names = self._game_summary(
            period_start,
            now,
            guild_id=guild_id,
        )
        refuge_event_count, refuge_event_labels = await self._refuge_summary(
            period_start, now
        )

        try:
            last_issue_number = max(0, int(state.get("last_issue_number", 0)))
        except (TypeError, ValueError):
            last_issue_number = 0

        return RefugeJournalIssue(
            publication_key=publication_key_for(now),
            issue_number=last_issue_number + 1,
            period_start=period_start,
            period_end=now,
            users_snapshot=current,
            total_xp=total("xp_earned"),
            total_messages=total("messages"),
            total_voice_seconds=total("voice_seconds"),
            casino_bets=total("casino_bets"),
            casino_net=total("casino_net"),
            xp_leader=_leader(deltas, "xp_earned"),
            messages_leader=_leader(deltas, "messages"),
            voice_leader=_leader(deltas, "voice_seconds"),
            achievement_count=achievement_count,
            achievement_highlights=achievement_highlights,
            game_event_count=game_event_count,
            game_participations=game_participations,
            game_names=game_names,
            refuge_event_count=refuge_event_count,
            refuge_event_labels=refuge_event_labels,
        )


refuge_journal_service = RefugeJournalService()


__all__ = [
    "JOURNAL_FIELDS",
    "JournalAchievement",
    "JournalLeader",
    "RefugeJournalIssue",
    "RefugeJournalService",
    "publication_key_for",
    "refuge_journal_service",
]
