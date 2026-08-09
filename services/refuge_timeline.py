from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Final, Mapping

from models.refuge_world import (
    RefugeHistoricalEvent,
    RefugePanelState,
    RefugeWorldSnapshot,
    RefugeWorldState,
)
from rendering.refuge_construction import (
    RefugeConstructionRenderer,
    refuge_construction_renderer,
)
from rendering.refuge_world import RefugeRenderContext
from services.refuge_world_coordination import refuge_world_mutation_lock
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from utils.seasons import season_bounds, season_id_for, season_label


TIMELINE_STATE_KEY: Final[str] = "refuge_timeline"
_TIMELINE_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class RefugeTimelineChapter:
    season_id: str
    label: str
    captured_at: str
    state: RefugeWorldState
    context: RefugeRenderContext
    chapter_event_count: int
    chapter_event_labels: tuple[str, ...]
    building_count: int
    monument_count: int
    fire_level: int
    hall_level: int
    casino_level: int
    construction_label: str | None


@dataclass(frozen=True, slots=True)
class RefugeTimelineSnapshot:
    current_season_id: str
    current_season_label: str
    chapters: tuple[RefugeTimelineChapter, ...]
    selected_season_id: str | None

    @property
    def selected(self) -> RefugeTimelineChapter | None:
        if self.selected_season_id is None:
            return None
        return next(
            (
                chapter
                for chapter in self.chapters
                if chapter.season_id == self.selected_season_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class RefugeTimelineSyncResult:
    state: RefugeWorldState
    archived_season_id: str | None
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


def _timeline_meta(state: RefugeWorldState) -> dict[str, object]:
    raw = state.state.get(TIMELINE_STATE_KEY, {})
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "version": _TIMELINE_VERSION,
        "enabled_at": str(source.get("enabled_at") or "") or None,
        "active_season_id": str(source.get("active_season_id") or "") or None,
        "last_archived_season_id": (
            str(source.get("last_archived_season_id") or "") or None
        ),
    }


def _state_with_meta(
    state: RefugeWorldState,
    meta: Mapping[str, object],
) -> RefugeWorldState:
    payload = dict(state.state)
    payload[TIMELINE_STATE_KEY] = dict(meta)
    return replace(state, state=payload)


def _event_ids_at_season_end(
    events: tuple[RefugeHistoricalEvent, ...],
    season_id: str,
) -> tuple[str, ...]:
    _, season_end = season_bounds(season_id)
    end_utc = season_end.astimezone(timezone.utc)
    selected: list[tuple[datetime, str]] = []
    for event in events:
        occurred = _parse_timestamp(event.occurred_at)
        if occurred is None or occurred >= end_utc:
            continue
        selected.append((occurred, event.event_id))
    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(event_id for _occurred, event_id in selected)


def _snapshot_context(snapshot: RefugeWorldSnapshot) -> RefugeRenderContext:
    _start, end = season_bounds(snapshot.season_id)
    final_moment = end - timedelta(microseconds=1)
    return RefugeRenderContext.from_datetime(final_moment)


def _building_level(state: RefugeWorldState, building_id: str) -> int:
    building = next(
        (
            candidate
            for candidate in state.buildings
            if candidate.building_id == building_id
        ),
        None,
    )
    return max(0, int(building.level)) if building is not None else 0


def _event_label(event: RefugeHistoricalEvent) -> str:
    explicit = event.data.get("name")
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
    if event.event_type.endswith("secret_discovered"):
        return "Un mystère du Refuge a été découvert"
    return labels.get(event.event_type, "Un événement a marqué le Refuge")


def _chapter_events(
    state: RefugeWorldState,
    season_id: str,
) -> tuple[RefugeHistoricalEvent, ...]:
    start, end = season_bounds(season_id)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    selected: list[tuple[datetime, RefugeHistoricalEvent]] = []
    for event in state.events:
        occurred = _parse_timestamp(event.occurred_at)
        if occurred is None or not start_utc <= occurred < end_utc:
            continue
        selected.append((occurred, event))
    selected.sort(key=lambda item: (item[0], item[1].event_id), reverse=True)
    return tuple(event for _occurred, event in selected)


def _construction_label(state: RefugeWorldState) -> str | None:
    construction = state.active_construction
    if construction is None:
        return None
    project_name = construction.data.get("project_name") or construction.data.get("name")
    if project_name:
        return str(project_name)
    if construction.project_id:
        return str(construction.project_id)
    return "Chantier en cours"


class RefugeTimelineService:
    """Archive immutable monthly Refuge chapters and render them on demand."""

    def __init__(
        self,
        *,
        world_store: RefugeWorldStore = refuge_world_store,
        renderer: RefugeConstructionRenderer = refuge_construction_renderer,
    ) -> None:
        self.world_store = world_store
        self.renderer = renderer

    async def sync(self, *, at: datetime | None = None) -> RefugeTimelineSyncResult:
        now = _aware_utc(at)
        async with refuge_world_mutation_lock():
            return await self.sync_under_world_lock(at=now)

    async def sync_under_world_lock(
        self,
        *,
        at: datetime | None = None,
    ) -> RefugeTimelineSyncResult:
        """Synchronize while the caller already owns the Refuge mutation lock."""

        now = _aware_utc(at)
        state = await self.world_store.initialize(created_at=now)
        current_season_id = season_id_for(now)
        meta = _timeline_meta(state)
        active_season_id = meta.get("active_season_id")

        if not active_season_id:
            meta["enabled_at"] = now.isoformat()
            meta["active_season_id"] = current_season_id
            updated = _state_with_meta(state, meta)
            saved = await self.world_store.save_state(updated)
            return RefugeTimelineSyncResult(saved, None, True)

        active_season_id = str(active_season_id)
        if active_season_id == current_season_id:
            return RefugeTimelineSyncResult(state, None, False)

        # Clock rollback must never rewrite an already opened historical chapter.
        if active_season_id > current_season_id:
            return RefugeTimelineSyncResult(state, None, False)

        existing = next(
            (
                snapshot
                for snapshot in state.snapshots
                if snapshot.season_id == active_season_id
            ),
            None,
        )
        archived_id: str | None = None
        snapshots = state.snapshots
        if existing is None:
            snapshot = RefugeWorldSnapshot(
                season_id=active_season_id,
                captured_at=now.isoformat(),
                buildings=state.buildings,
                event_ids=_event_ids_at_season_end(state.events, active_season_id),
                active_construction=state.active_construction,
                state=dict(state.state),
            )
            snapshots = tuple(
                sorted(
                    (*state.snapshots, snapshot),
                    key=lambda item: item.season_id,
                )
            )
            archived_id = active_season_id

        meta["active_season_id"] = current_season_id
        meta["last_archived_season_id"] = active_season_id
        updated = replace(
            _state_with_meta(state, meta),
            snapshots=snapshots,
        )
        saved = await self.world_store.save_state(updated)
        return RefugeTimelineSyncResult(saved, archived_id, True)

    def _restore_snapshot_state(
        self,
        world: RefugeWorldState,
        snapshot: RefugeWorldSnapshot,
    ) -> RefugeWorldState:
        event_ids = set(snapshot.event_ids)
        historical_events = tuple(
            event for event in world.events if event.event_id in event_ids
        )
        return RefugeWorldState(
            schema_version=world.schema_version,
            created_at=world.created_at,
            buildings=snapshot.buildings,
            events=historical_events,
            snapshots=(),
            panel=RefugePanelState(),
            active_construction=snapshot.active_construction,
            state=dict(snapshot.state),
        )

    def _chapter_from_snapshot(
        self,
        world: RefugeWorldState,
        snapshot: RefugeWorldSnapshot,
    ) -> RefugeTimelineChapter:
        restored = self._restore_snapshot_state(world, snapshot)
        chapter_events = _chapter_events(restored, snapshot.season_id)
        monuments = tuple(
            building
            for building in restored.buildings
            if building.building_id.startswith("monument:")
        )
        return RefugeTimelineChapter(
            season_id=snapshot.season_id,
            label=season_label(snapshot.season_id),
            captured_at=snapshot.captured_at,
            state=restored,
            context=_snapshot_context(snapshot),
            chapter_event_count=len(chapter_events),
            chapter_event_labels=tuple(
                _event_label(event) for event in chapter_events[:5]
            ),
            building_count=len(restored.buildings),
            monument_count=len(monuments),
            fire_level=_building_level(restored, "fire"),
            hall_level=_building_level(restored, "hall"),
            casino_level=_building_level(restored, "casino"),
            construction_label=_construction_label(restored),
        )

    async def get_timeline(
        self,
        *,
        selected_season_id: str | None = None,
        at: datetime | None = None,
    ) -> RefugeTimelineSnapshot:
        sync_result = await self.sync(at=at)
        world = sync_result.state
        chapters = tuple(
            self._chapter_from_snapshot(world, snapshot)
            for snapshot in sorted(
                world.snapshots,
                key=lambda item: item.season_id,
                reverse=True,
            )
        )
        selected = selected_season_id
        if selected is None and chapters:
            selected = chapters[0].season_id
        if selected is not None and not any(
            chapter.season_id == selected for chapter in chapters
        ):
            selected = chapters[0].season_id if chapters else None
        current_season_id = season_id_for(_aware_utc(at))
        return RefugeTimelineSnapshot(
            current_season_id=current_season_id,
            current_season_label=season_label(current_season_id),
            chapters=chapters,
            selected_season_id=selected,
        )

    async def render_chapter_png(self, chapter: RefugeTimelineChapter) -> bytes:
        return await self.renderer.render_png_async(
            chapter.state,
            context=chapter.context,
        )


refuge_timeline_service = RefugeTimelineService()


__all__ = [
    "TIMELINE_STATE_KEY",
    "RefugeTimelineChapter",
    "RefugeTimelineService",
    "RefugeTimelineSnapshot",
    "RefugeTimelineSyncResult",
    "refuge_timeline_service",
]
