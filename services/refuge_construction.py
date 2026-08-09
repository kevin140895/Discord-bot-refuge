from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Final, Mapping, Sequence

from models.refuge_world import (
    RefugeBuildingState,
    RefugeConstructionState,
    RefugeHistoricalEvent,
    RefugeWorldState,
)
from services.refuge_world_coordination import refuge_world_mutation_lock
from storage.community_goal_store import CommunityGoalStore, community_goal_store
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store


CONSTRUCTION_TIE_EXTENSION_HOURS: Final[int] = 24
CONSTRUCTION_STATUS_VOTING: Final[str] = "voting"
CONSTRUCTION_STATUS_TIE_BREAK: Final[str] = "tie_break"
CONSTRUCTION_STATUS_BUILDING: Final[str] = "building"

_CONSTRUCTION_ENABLED_AT_KEY: Final[str] = "construction_enabled_at"
_CONSTRUCTION_PENDING_GOALS_KEY: Final[str] = "construction_pending_goals"
_CONSTRUCTION_CONSUMED_GOALS_KEY: Final[str] = "construction_consumed_goal_ids"


@dataclass(frozen=True, slots=True)
class RefugeConstructionProject:
    project_id: str
    name: str
    emoji: str
    description: str
    building_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "emoji": self.emoji,
            "description": self.description,
            "building_id": self.building_id,
        }


CONSTRUCTION_PROJECTS: Final[tuple[RefugeConstructionProject, ...]] = (
    RefugeConstructionProject(
        project_id="star_observatory",
        name="Observatoire des Étoiles",
        emoji="🔭",
        description="Un observatoire permanent tourné vers le ciel du Refuge.",
        building_id="monument:star_observatory",
    ),
    RefugeConstructionProject(
        project_id="memory_garden",
        name="Jardin des Souvenirs",
        emoji="🌿",
        description="Un jardin permanent dédié aux traces laissées par la communauté.",
        building_id="monument:memory_garden",
    ),
    RefugeConstructionProject(
        project_id="lantern_tower",
        name="Tour des Lanternes",
        emoji="🏮",
        description="Une tour permanente dont les lanternes veillent sur le Refuge.",
        building_id="monument:lantern_tower",
    ),
)
PROJECT_BY_ID: Final[dict[str, RefugeConstructionProject]] = {
    project.project_id: project for project in CONSTRUCTION_PROJECTS
}


@dataclass(frozen=True, slots=True)
class RefugeConstructionConfig:
    vote_hours: int = 72
    build_hours: int = 168

    @classmethod
    def from_env(cls) -> "RefugeConstructionConfig":
        return cls(
            vote_hours=_env_positive_int("REFUGE_CONSTRUCTION_VOTE_HOURS", 72),
            build_hours=_env_positive_int("REFUGE_CONSTRUCTION_BUILD_HOURS", 168),
        )


@dataclass(frozen=True, slots=True)
class RefugeConstructionOption:
    project_id: str
    name: str
    emoji: str
    description: str


@dataclass(frozen=True, slots=True)
class RefugeConstructionSnapshot:
    active: bool
    status: str | None
    construction_id: str | None
    source_goal_id: str | None
    source_goal_title: str | None
    options: tuple[RefugeConstructionOption, ...]
    allowed_project_ids: tuple[str, ...]
    user_vote: str | None
    project_id: str | None
    project_name: str | None
    opened_at: str | None
    closes_at: str | None
    started_at: str | None
    completes_at: str | None
    progress_percent: int
    winner_method: str | None
    final_results: tuple[tuple[str, int], ...]
    completed_monuments: tuple[str, ...]


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(at: datetime | None = None) -> str:
    return _aware_utc(at).isoformat()


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


def _world_data(state: RefugeWorldState) -> dict[str, Any]:
    return dict(state.state)


def _pending_goals(state: RefugeWorldState) -> list[dict[str, Any]]:
    raw = state.state.get(_CONSTRUCTION_PENDING_GOALS_KEY, ())
    if not isinstance(raw, (list, tuple)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _consumed_goal_ids(state: RefugeWorldState) -> set[str]:
    raw = state.state.get(_CONSTRUCTION_CONSUMED_GOALS_KEY, ())
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in raw if str(item).strip()}


def _goal_snapshot(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    goal_id = str(goal.get("id", "")).strip()
    completed_at = str(goal.get("completed_at", "")).strip()
    if not goal_id or not completed_at:
        return None
    return {
        "id": goal_id,
        "title": str(goal.get("title") or goal.get("metric_key") or "Objectif communautaire"),
        "metric_key": str(goal.get("metric_key", "")),
        "completed_at": completed_at,
        "final_progress": int(goal.get("final_progress") or 0),
    }


def _projects_from_construction(
    construction: RefugeConstructionState,
) -> tuple[RefugeConstructionOption, ...]:
    raw = construction.data.get("projects", ())
    options: list[RefugeConstructionOption] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            project_id = str(item.get("project_id", "")).strip()
            name = str(item.get("name", "")).strip()
            if not project_id or not name:
                continue
            options.append(
                RefugeConstructionOption(
                    project_id=project_id,
                    name=name,
                    emoji=str(item.get("emoji") or "🏗️"),
                    description=str(item.get("description") or "").strip(),
                )
            )
    return tuple(options)


def _votes(construction: RefugeConstructionState) -> dict[str, str]:
    raw = construction.data.get("votes", {})
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(user_id): str(project_id)
        for user_id, project_id in raw.items()
        if str(user_id).strip() and str(project_id).strip()
    }


def _vote_counts(
    construction: RefugeConstructionState,
    project_ids: Sequence[str],
) -> dict[str, int]:
    allowed = tuple(dict.fromkeys(str(item) for item in project_ids))
    counts = {project_id: 0 for project_id in allowed}
    for project_id in _votes(construction).values():
        if project_id in counts:
            counts[project_id] += 1
    return counts


def _leaders(counts: Mapping[str, int]) -> tuple[str, ...]:
    if not counts:
        return ()
    best = max(int(value) for value in counts.values())
    return tuple(sorted(key for key, value in counts.items() if int(value) == best))


def _append_event(
    state: RefugeWorldState,
    event: RefugeHistoricalEvent,
) -> RefugeWorldState:
    if any(existing.event_id == event.event_id for existing in state.events):
        return state
    return replace(state, events=state.events + (event,))


def _replace_or_add_building(
    state: RefugeWorldState,
    building: RefugeBuildingState,
) -> RefugeWorldState:
    items = {item.building_id: item for item in state.buildings}
    items[building.building_id] = building
    return replace(
        state,
        buildings=tuple(sorted(items.values(), key=lambda item: item.building_id)),
    )


def _completed_monument_names(state: RefugeWorldState) -> tuple[str, ...]:
    names: list[tuple[str, str]] = []
    for building in state.buildings:
        if not building.building_id.startswith("monument:"):
            continue
        name = str(building.state.get("project_name") or building.building_id).strip()
        names.append((building.unlocked_at or "", name))
    names.sort()
    return tuple(name for _, name in names)


def _construction_progress(
    construction: RefugeConstructionState,
    now: datetime,
) -> int:
    if construction.status != CONSTRUCTION_STATUS_BUILDING:
        return 0
    started = _parse_timestamp(construction.started_at)
    completes = _parse_timestamp(construction.completes_at)
    if started is None or completes is None or completes <= started:
        return 0
    if now <= started:
        return 0
    if now >= completes:
        return 100
    ratio = (now - started).total_seconds() / (completes - started).total_seconds()
    return max(0, min(100, int(ratio * 100)))


class RefugeConstructionService:
    """Turn real completed community goals into one persistent build right."""

    def __init__(
        self,
        *,
        world_store: RefugeWorldStore = refuge_world_store,
        goal_store: CommunityGoalStore = community_goal_store,
        chooser: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.world_store = world_store
        self.goal_store = goal_store
        self._chooser = chooser or secrets.choice

    def _activate(
        self,
        state: RefugeWorldState,
        *,
        now: datetime,
    ) -> tuple[RefugeWorldState, bool]:
        data = _world_data(state)
        if data.get(_CONSTRUCTION_ENABLED_AT_KEY):
            return state, False
        data[_CONSTRUCTION_ENABLED_AT_KEY] = _iso(now)
        data.setdefault(_CONSTRUCTION_PENDING_GOALS_KEY, [])
        data.setdefault(_CONSTRUCTION_CONSUMED_GOALS_KEY, [])
        return replace(state, state=data), True

    def _reconcile_completed(
        self,
        state: RefugeWorldState,
        goals: Sequence[Mapping[str, Any]],
    ) -> tuple[RefugeWorldState, bool]:
        enabled_at = _parse_timestamp(state.state.get(_CONSTRUCTION_ENABLED_AT_KEY))
        if enabled_at is None:
            return state, False

        pending = _pending_goals(state)
        queued_ids = {str(item.get("id", "")) for item in pending}
        consumed = _consumed_goal_ids(state)
        active_source = (
            str(state.active_construction.data.get("source_goal_id", ""))
            if state.active_construction is not None
            else ""
        )
        added = False

        eligible: list[dict[str, Any]] = []
        for goal in goals:
            snapshot = _goal_snapshot(goal)
            if snapshot is None:
                continue
            completed_at = _parse_timestamp(snapshot["completed_at"])
            goal_id = str(snapshot["id"])
            if completed_at is None or completed_at < enabled_at:
                continue
            if goal_id in consumed or goal_id in queued_ids or goal_id == active_source:
                continue
            eligible.append(snapshot)

        eligible.sort(key=lambda item: (str(item["completed_at"]), str(item["id"])))
        if eligible:
            pending.extend(eligible)
            added = True

        if not added:
            return state, False
        data = _world_data(state)
        data[_CONSTRUCTION_PENDING_GOALS_KEY] = pending
        return replace(state, state=data), True

    def _open_next(
        self,
        state: RefugeWorldState,
        *,
        now: datetime,
        config: RefugeConstructionConfig,
    ) -> tuple[RefugeWorldState, bool]:
        if state.active_construction is not None:
            return state, False
        pending = _pending_goals(state)
        if not pending:
            return state, False

        goal = pending.pop(0)
        goal_id = str(goal["id"])
        construction_id = f"goal:{goal_id}"
        data = {
            "source_goal_id": goal_id,
            "source_goal_title": str(goal.get("title") or "Objectif communautaire"),
            "source_goal_metric": str(goal.get("metric_key") or ""),
            "projects": [project.to_dict() for project in CONSTRUCTION_PROJECTS],
            "votes": {},
            "winner_method": None,
            "final_results": {},
            "tied_project_ids": [],
        }
        construction = RefugeConstructionState(
            construction_id=construction_id,
            status=CONSTRUCTION_STATUS_VOTING,
            opened_at=_iso(now),
            closes_at=_iso(now + timedelta(hours=config.vote_hours)),
            data=data,
        )

        world_data = _world_data(state)
        consumed = _consumed_goal_ids(state)
        consumed.add(goal_id)
        world_data[_CONSTRUCTION_PENDING_GOALS_KEY] = pending
        world_data[_CONSTRUCTION_CONSUMED_GOALS_KEY] = sorted(consumed)

        updated = replace(state, state=world_data, active_construction=construction)
        updated = _append_event(
            updated,
            RefugeHistoricalEvent(
                event_id=f"construction:{construction_id}:opened",
                event_type="construction_vote_opened",
                occurred_at=_iso(now),
                data={
                    "construction_id": construction_id,
                    "source_goal_id": goal_id,
                    "name": "Un chantier s’est ouvert",
                },
            ),
        )
        return updated, True

    def _start_building(
        self,
        state: RefugeWorldState,
        construction: RefugeConstructionState,
        *,
        winner_id: str,
        winner_method: str,
        now: datetime,
        config: RefugeConstructionConfig,
    ) -> RefugeWorldState:
        project = PROJECT_BY_ID.get(winner_id)
        if project is None:
            raise RuntimeError(f"unknown construction winner: {winner_id}")

        all_ids = tuple(project.project_id for project in CONSTRUCTION_PROJECTS)
        counts = _vote_counts(construction, all_ids)
        data = dict(construction.data)
        data["winner_method"] = winner_method
        data["final_results"] = dict(sorted(counts.items()))
        data["project_name"] = project.name
        data["project_description"] = project.description
        data["building_id"] = project.building_id

        active = replace(
            construction,
            status=CONSTRUCTION_STATUS_BUILDING,
            project_id=winner_id,
            started_at=_iso(now),
            completes_at=_iso(now + timedelta(hours=config.build_hours)),
            data=data,
        )
        updated = replace(state, active_construction=active)
        return _append_event(
            updated,
            RefugeHistoricalEvent(
                event_id=f"construction:{construction.construction_id}:started",
                event_type="construction_started",
                occurred_at=_iso(now),
                data={
                    "construction_id": construction.construction_id,
                    "project_id": winner_id,
                    "name": f"Construction lancée · {project.name}",
                    "winner_method": winner_method,
                },
            ),
        )

    def _resolve_vote(
        self,
        state: RefugeWorldState,
        construction: RefugeConstructionState,
        *,
        now: datetime,
        config: RefugeConstructionConfig,
    ) -> RefugeWorldState:
        all_ids = tuple(project.project_id for project in CONSTRUCTION_PROJECTS)

        if construction.status == CONSTRUCTION_STATUS_VOTING:
            counts = _vote_counts(construction, all_ids)
            leaders = _leaders(counts)
            if len(leaders) == 1:
                return self._start_building(
                    state,
                    construction,
                    winner_id=leaders[0],
                    winner_method="vote",
                    now=now,
                    config=config,
                )

            data = dict(construction.data)
            data["tied_project_ids"] = list(leaders)
            extended = replace(
                construction,
                status=CONSTRUCTION_STATUS_TIE_BREAK,
                closes_at=_iso(now + timedelta(hours=CONSTRUCTION_TIE_EXTENSION_HOURS)),
                data=data,
            )
            updated = replace(state, active_construction=extended)
            return _append_event(
                updated,
                RefugeHistoricalEvent(
                    event_id=f"construction:{construction.construction_id}:tie",
                    event_type="construction_vote_tied",
                    occurred_at=_iso(now),
                    data={
                        "construction_id": construction.construction_id,
                        "project_ids": list(leaders),
                        "extension_hours": CONSTRUCTION_TIE_EXTENSION_HOURS,
                        "name": "Le vote du chantier est à égalité",
                    },
                ),
            )

        tied_raw = construction.data.get("tied_project_ids", ())
        tied_ids = (
            tuple(
                project_id
                for project_id in (str(item) for item in tied_raw)
                if project_id in PROJECT_BY_ID
            )
            if isinstance(tied_raw, (list, tuple))
            else ()
        )
        if not tied_ids:
            tied_ids = all_ids

        counts = _vote_counts(construction, tied_ids)
        leaders = _leaders(counts)
        if len(leaders) == 1:
            winner_id = leaders[0]
            method = "tie_extension_vote"
        else:
            winner_id = str(self._chooser(leaders))
            if winner_id not in leaders:
                raise RuntimeError("tie chooser returned an ineligible project")
            method = "random_tie"

        return self._start_building(
            state,
            construction,
            winner_id=winner_id,
            winner_method=method,
            now=now,
            config=config,
        )

    def _complete_building(
        self,
        state: RefugeWorldState,
        construction: RefugeConstructionState,
        *,
        now: datetime,
    ) -> RefugeWorldState:
        project = PROJECT_BY_ID.get(str(construction.project_id or ""))
        if project is None:
            raise RuntimeError("construction completion has no supported project")

        building = RefugeBuildingState(
            building_id=project.building_id,
            level=1,
            unlocked_at=_iso(now),
            state={
                "project_id": project.project_id,
                "project_name": project.name,
                "description": project.description,
                "construction_id": construction.construction_id,
                "source_goal_id": construction.data.get("source_goal_id"),
            },
        )
        updated = _replace_or_add_building(state, building)
        updated = replace(updated, active_construction=None)
        return _append_event(
            updated,
            RefugeHistoricalEvent(
                event_id=f"construction:{construction.construction_id}:completed",
                event_type="construction_completed",
                occurred_at=_iso(now),
                data={
                    "construction_id": construction.construction_id,
                    "project_id": project.project_id,
                    "building_id": project.building_id,
                    "source_goal_id": construction.data.get("source_goal_id"),
                    "winner_method": construction.data.get("winner_method"),
                    "name": f"{project.name} a été inauguré",
                },
            ),
        )

    def _advance(
        self,
        state: RefugeWorldState,
        *,
        now: datetime,
        config: RefugeConstructionConfig,
    ) -> tuple[RefugeWorldState, bool]:
        changed = False
        while True:
            if state.active_construction is None:
                state, opened = self._open_next(state, now=now, config=config)
                changed = changed or opened
                return state, changed

            construction = state.active_construction
            if construction.status in {
                CONSTRUCTION_STATUS_VOTING,
                CONSTRUCTION_STATUS_TIE_BREAK,
            }:
                closes_at = _parse_timestamp(construction.closes_at)
                if closes_at is None or now < closes_at:
                    return state, changed
                state = self._resolve_vote(state, construction, now=now, config=config)
                changed = True
                continue

            if construction.status == CONSTRUCTION_STATUS_BUILDING:
                completes_at = _parse_timestamp(construction.completes_at)
                if completes_at is None or now < completes_at:
                    return state, changed
                state = self._complete_building(state, construction, now=now)
                changed = True
                continue

            return state, changed

    async def sync(
        self,
        *,
        at: datetime | None = None,
        config: RefugeConstructionConfig | None = None,
    ) -> RefugeConstructionSnapshot:
        now = _aware_utc(at)
        resolved_config = config or RefugeConstructionConfig.from_env()
        completed_goals = await self.goal_store.list_goals(status="completed")

        async with refuge_world_mutation_lock():
            state = await self.world_store.initialize(created_at=now)
            changed = False

            state, activated = self._activate(state, now=now)
            changed = changed or activated
            state, reconciled = self._reconcile_completed(state, completed_goals)
            changed = changed or reconciled
            state, advanced = self._advance(state, now=now, config=resolved_config)
            changed = changed or advanced

            if changed:
                state = await self.world_store.save_state(state)

            return self._snapshot(state, user_id=None, now=now)

    async def get_snapshot(
        self,
        user_id: int | None = None,
        *,
        at: datetime | None = None,
        config: RefugeConstructionConfig | None = None,
    ) -> RefugeConstructionSnapshot:
        now = _aware_utc(at)
        await self.sync(at=now, config=config)
        async with refuge_world_mutation_lock():
            state = await self.world_store.get_state()
            return self._snapshot(state, user_id=user_id, now=now)

    async def cast_vote(
        self,
        user_id: int,
        project_id: str,
        *,
        at: datetime | None = None,
        config: RefugeConstructionConfig | None = None,
    ) -> RefugeConstructionSnapshot:
        now = _aware_utc(at)
        resolved_config = config or RefugeConstructionConfig.from_env()
        await self.sync(at=now, config=resolved_config)

        normalized_user_id = int(user_id)
        if normalized_user_id <= 0:
            raise ValueError("user_id must be positive")

        async with refuge_world_mutation_lock():
            state = await self.world_store.get_state()
            construction = state.active_construction
            if construction is None or construction.status not in {
                CONSTRUCTION_STATUS_VOTING,
                CONSTRUCTION_STATUS_TIE_BREAK,
            }:
                raise ValueError("construction vote is not open")

            options = _projects_from_construction(construction)
            option_ids = tuple(option.project_id for option in options)
            if construction.status == CONSTRUCTION_STATUS_TIE_BREAK:
                raw_tied = construction.data.get("tied_project_ids", ())
                allowed = (
                    tuple(
                        item
                        for item in (str(value) for value in raw_tied)
                        if item in option_ids
                    )
                    if isinstance(raw_tied, (list, tuple))
                    else ()
                )
            else:
                allowed = option_ids

            normalized_project_id = str(project_id).strip()
            if normalized_project_id not in allowed:
                raise ValueError("project is not eligible for this vote")

            data = dict(construction.data)
            votes = _votes(construction)
            votes[str(normalized_user_id)] = normalized_project_id
            data["votes"] = votes
            updated = replace(
                state,
                active_construction=replace(construction, data=data),
            )
            updated = await self.world_store.save_state(updated)
            return self._snapshot(updated, user_id=normalized_user_id, now=now)

    def _snapshot(
        self,
        state: RefugeWorldState,
        *,
        user_id: int | None,
        now: datetime,
    ) -> RefugeConstructionSnapshot:
        construction = state.active_construction
        completed = _completed_monument_names(state)
        if construction is None:
            return RefugeConstructionSnapshot(
                active=False,
                status=None,
                construction_id=None,
                source_goal_id=None,
                source_goal_title=None,
                options=(),
                allowed_project_ids=(),
                user_vote=None,
                project_id=None,
                project_name=None,
                opened_at=None,
                closes_at=None,
                started_at=None,
                completes_at=None,
                progress_percent=0,
                winner_method=None,
                final_results=(),
                completed_monuments=completed,
            )

        options = _projects_from_construction(construction)
        option_ids = tuple(option.project_id for option in options)
        if construction.status == CONSTRUCTION_STATUS_TIE_BREAK:
            raw_tied = construction.data.get("tied_project_ids", ())
            allowed = (
                tuple(
                    item
                    for item in (str(value) for value in raw_tied)
                    if item in option_ids
                )
                if isinstance(raw_tied, (list, tuple))
                else ()
            )
        elif construction.status == CONSTRUCTION_STATUS_VOTING:
            allowed = option_ids
        else:
            allowed = ()

        votes = _votes(construction)
        user_vote = votes.get(str(user_id)) if user_id is not None else None
        if user_vote not in allowed and construction.status == CONSTRUCTION_STATUS_TIE_BREAK:
            user_vote = None

        raw_results = construction.data.get("final_results", {})
        final_results = (
            tuple(sorted((str(key), int(value)) for key, value in raw_results.items()))
            if isinstance(raw_results, Mapping)
            else ()
        )

        return RefugeConstructionSnapshot(
            active=True,
            status=construction.status,
            construction_id=construction.construction_id,
            source_goal_id=str(construction.data.get("source_goal_id") or "") or None,
            source_goal_title=str(construction.data.get("source_goal_title") or "") or None,
            options=options,
            allowed_project_ids=allowed,
            user_vote=user_vote,
            project_id=construction.project_id,
            project_name=str(construction.data.get("project_name") or "") or None,
            opened_at=construction.opened_at,
            closes_at=construction.closes_at,
            started_at=construction.started_at,
            completes_at=construction.completes_at,
            progress_percent=_construction_progress(construction, now),
            winner_method=str(construction.data.get("winner_method") or "") or None,
            final_results=final_results,
            completed_monuments=completed,
        )


refuge_construction_service = RefugeConstructionService()


__all__ = [
    "CONSTRUCTION_PROJECTS",
    "CONSTRUCTION_STATUS_BUILDING",
    "CONSTRUCTION_STATUS_TIE_BREAK",
    "CONSTRUCTION_STATUS_VOTING",
    "CONSTRUCTION_TIE_EXTENSION_HOURS",
    "PROJECT_BY_ID",
    "RefugeConstructionConfig",
    "RefugeConstructionOption",
    "RefugeConstructionProject",
    "RefugeConstructionService",
    "RefugeConstructionSnapshot",
    "refuge_construction_service",
]
