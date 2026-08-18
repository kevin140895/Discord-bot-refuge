from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Mapping

from models.refuge_world import RefugeHistoricalEvent, RefugeWorldState
from rendering.refuge_construction import (
    RefugeConstructionRenderer,
    construction_scene_signature,
    refuge_construction_renderer,
)
from rendering.refuge_live_activity import apply_refuge_activity_overlay
from rendering.refuge_world import RefugeRenderContext
from services.refuge_casino import RefugeCasinoService, refuge_casino_service
from services.refuge_fire import RefugeFireService, refuge_fire_service
from services.refuge_hall import RefugeHallService, refuge_hall_service
from services.refuge_secrets import RefugeSecretsService, refuge_secrets_service
from services.refuge_timeline import RefugeTimelineService, refuge_timeline_service
from services.refuge_world_coordination import refuge_world_mutation_lock
from utils.seasons import season_id_for, season_label


_FIRE_INTENSITY_NAMES: Final[Mapping[str, str]] = {
    "low": "Calme",
    "normal": "Vivant",
    "high": "Ardent",
}
_CONSTRUCTION_STATUS_NAMES: Final[Mapping[str, str]] = {
    "open": "Vote en cours",
    "voting": "Vote en cours",
    "tie_break": "Départage en cours",
    "building": "Construction en cours",
    "constructing": "Construction en cours",
    "completed": "Inauguration prête",
    "complete": "Inauguration prête",
}


@dataclass(frozen=True, slots=True)
class RefugePanelSnapshot:
    state: RefugeWorldState
    context: RefugeRenderContext
    season_id: str
    season_label: str
    fire_level: int
    fire_name: str
    fire_intensity: str
    fire_intensity_name: str
    hall_level: int
    hall_name: str
    casino_level: int
    casino_name: str
    casino_fortune: str
    casino_fortune_name: str
    casino_is_open: bool
    construction_label: str
    latest_event_id: str | None
    latest_event_label: str | None
    visual_signature: str
    summary_signature: str
    changed: bool


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def construction_label(state: RefugeWorldState) -> str:
    construction = state.active_construction
    if construction is None:
        return "Aucun chantier actif"

    raw_name = construction.data.get("project_name") or construction.data.get("name")
    project_name = str(raw_name).strip() if raw_name else ""
    status = _CONSTRUCTION_STATUS_NAMES.get(
        str(construction.status).strip().lower(),
        "Chantier actif",
    )
    return f"{status} · {project_name}" if project_name else status


def _event_timestamp(event: RefugeHistoricalEvent) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(event.occurred_at).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_event(state: RefugeWorldState) -> RefugeHistoricalEvent | None:
    if not state.events:
        return None
    return max(
        state.events,
        key=lambda event: (_event_timestamp(event), event.event_id),
    )


def event_label(event: RefugeHistoricalEvent | None) -> str | None:
    if event is None:
        return None
    explicit_name = event.data.get("name")
    if explicit_name:
        return str(explicit_name)

    if event.event_type == "building_level_reached":
        building = str(event.data.get("building_id", "")).strip()
        level = event.data.get("level")
        labels = {
            "fire": "Le Feu",
            "hall": "Le Hall",
            "casino": "Le Casino",
        }
        subject = labels.get(building, "Le Refuge")
        try:
            return f"{subject} a atteint le niveau {int(level)}"
        except (TypeError, ValueError):
            return f"{subject} a évolué"

    if event.event_type == "casino_jackpot_observed":
        try:
            tier = int(event.data.get("tier", 0))
        except (TypeError, ValueError):
            tier = 0
        return f"Jackpot machine à sous · {tier} XP" if tier else "Jackpot au Casino"

    if event.event_type == "hall_gallery_marker":
        return "Une nouvelle trace est entrée au Hall"
    if event.event_type.endswith("secret_discovered"):
        return "Un mystère du Refuge a été découvert"
    if event.event_type == "construction_vote_opened":
        return "Un nouveau chantier s’est ouvert"
    if event.event_type == "construction_vote_tied":
        return "Le vote du chantier est à égalité"
    if event.event_type == "construction_started":
        return "Une construction a commencé"
    if event.event_type == "construction_completed":
        return "Un monument a été inauguré"
    return "Un nouvel événement a marqué le Refuge"


def _summary_signature(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class RefugePanelService:
    """Orchestrate current Refuge systems for one public read model."""

    def __init__(
        self,
        *,
        fire_service: RefugeFireService = refuge_fire_service,
        hall_service: RefugeHallService = refuge_hall_service,
        casino_service: RefugeCasinoService = refuge_casino_service,
        timeline_service: RefugeTimelineService = refuge_timeline_service,
        secrets_service: RefugeSecretsService = refuge_secrets_service,
        renderer: RefugeConstructionRenderer = refuge_construction_renderer,
    ) -> None:
        self.fire_service = fire_service
        self.hall_service = hall_service
        self.casino_service = casino_service
        self.timeline_service = timeline_service
        self.secrets_service = secrets_service
        self.renderer = renderer

    async def evaluate(self, *, at: datetime | None = None) -> RefugePanelSnapshot:
        now = _aware_utc(at)
        async with refuge_world_mutation_lock():
            # REFUGE-011 archives the previous Paris calendar month before any
            # current-month mutation can touch the shared world state.
            await self.timeline_service.sync_under_world_lock(at=now)

            # Sequential evaluation is intentional: all systems share
            # RefugeWorldStore and each stage must observe the previous write.
            fire = await self.fire_service.evaluate(at=now)
            hall = await self.hall_service.evaluate(at=now)
            casino = await self.casino_service.evaluate(at=now)

            # Hidden discoveries are evaluated only after their source systems
            # have projected the latest real evidence into the world. The
            # service already runs under the shared mutation lock here.
            secrets = await self.secrets_service.sync_under_world_lock(at=now)
            state = secrets.state

            context = RefugeRenderContext.from_datetime(now)
            current_season = season_id_for(now)
            last_event = latest_event(state)
            last_event_label = event_label(last_event)
            build_label = construction_label(state)
            fire_intensity_name = _FIRE_INTENSITY_NAMES.get(
                fire.intensity,
                fire.intensity.capitalize(),
            )
            visual_signature = construction_scene_signature(state, context)
            summary_payload = {
                "season_id": current_season,
                "fire_level": fire.level,
                "fire_name": fire.level_name,
                "fire_intensity": fire.intensity,
                "hall_level": hall.level,
                "hall_name": hall.level_name,
                "casino_level": casino.level,
                "casino_name": casino.level_name,
                "casino_fortune": casino.fortune,
                "casino_open": casino.is_open,
                "construction": build_label,
                "latest_event_id": last_event.event_id if last_event else None,
            }

            return RefugePanelSnapshot(
                state=state,
                context=context,
                season_id=current_season,
                season_label=season_label(current_season),
                fire_level=fire.level,
                fire_name=fire.level_name,
                fire_intensity=fire.intensity,
                fire_intensity_name=fire_intensity_name,
                hall_level=hall.level,
                hall_name=hall.level_name,
                casino_level=casino.level,
                casino_name=casino.level_name,
                casino_fortune=casino.fortune,
                casino_fortune_name=casino.fortune_name,
                casino_is_open=casino.is_open,
                construction_label=build_label,
                latest_event_id=last_event.event_id if last_event else None,
                latest_event_label=last_event_label,
                visual_signature=visual_signature,
                summary_signature=_summary_signature(summary_payload),
                changed=bool(
                    fire.changed
                    or hall.changed
                    or casino.changed
                    or secrets.changed
                ),
            )

    async def render_png(
        self,
        snapshot: RefugePanelSnapshot,
        *,
        activity_key: str | None = None,
    ) -> bytes:
        png = await self.renderer.render_png_async(
            snapshot.state,
            context=snapshot.context,
        )
        if activity_key is None:
            return png
        return await asyncio.to_thread(
            apply_refuge_activity_overlay,
            png,
            activity_key=activity_key,
            context=snapshot.context,
        )


refuge_panel_service = RefugePanelService()


__all__ = [
    "RefugePanelService",
    "RefugePanelSnapshot",
    "construction_label",
    "event_label",
    "latest_event",
    "refuge_panel_service",
]
