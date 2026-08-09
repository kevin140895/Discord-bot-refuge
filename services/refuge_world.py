from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping, Sequence

from models.refuge_world import RefugeBuildingState, RefugeHistoricalEvent, RefugeWorldState
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class BuildingProgressionRule:
    building_id: str
    metric_key: str
    thresholds: tuple[int, ...] = ()
    minimum_level: int = 0
    event_from_level: int = 2

    def __post_init__(self) -> None:
        if not self.building_id.strip():
            raise ValueError("building_id is required")
        if not self.metric_key.strip():
            raise ValueError("metric_key is required")
        if self.minimum_level < 0:
            raise ValueError("minimum_level must be >= 0")
        if self.event_from_level < 1:
            raise ValueError("event_from_level must be >= 1")
        previous = -1
        for threshold in self.thresholds:
            if threshold < 0:
                raise ValueError("thresholds must be >= 0")
            if threshold <= previous:
                raise ValueError("thresholds must be strictly increasing")
            previous = threshold


@dataclass(frozen=True, slots=True)
class RefugeWorldEvaluation:
    state: RefugeWorldState
    changed: bool
    changed_buildings: tuple[str, ...]
    render_signature: str


def level_for_metric(
    value: int,
    *,
    thresholds: Sequence[int],
    minimum_level: int = 0,
) -> int:
    normalized = max(0, int(value))
    level = max(0, int(minimum_level))
    for threshold in thresholds:
        if normalized < int(threshold):
            break
        level += 1
    return level


def world_render_signature(state: RefugeWorldState) -> str:
    """Hash only fields that can affect the rendered current world."""

    payload = {
        "buildings": [
            {
                "building_id": building.building_id,
                "level": int(building.level),
                "state": building.state,
            }
            for building in sorted(
                state.buildings,
                key=lambda item: item.building_id,
            )
        ],
        "active_construction": (
            state.active_construction.to_dict()
            if state.active_construction is not None
            else None
        ),
        "state": state.state,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _event_id(building_id: str, level: int) -> str:
    return f"building:{building_id}:level:{level}"


class RefugeWorldService:
    """Apply configured progression rules to the persisted Refuge world."""

    def __init__(self, store: RefugeWorldStore = refuge_world_store) -> None:
        self.store = store

    async def evaluate(
        self,
        *,
        metrics: Mapping[str, int],
        rules: Sequence[BuildingProgressionRule],
        at: datetime | None = None,
    ) -> RefugeWorldEvaluation:
        now_iso = _utc_iso(at)
        current = await self.store.initialize(created_at=at)
        buildings = {
            building.building_id: building
            for building in current.buildings
        }
        events = list(current.events)
        existing_event_ids = {event.event_id for event in events}
        changed_buildings: list[str] = []
        rule_building_ids = [rule.building_id for rule in rules]
        if len(set(rule_building_ids)) != len(rule_building_ids):
            raise ValueError("only one progression rule is allowed per building")

        for rule in rules:
            metric_value = max(0, int(metrics.get(rule.metric_key, 0)))
            target_level = level_for_metric(
                metric_value,
                thresholds=rule.thresholds,
                minimum_level=rule.minimum_level,
            )
            existing = buildings.get(rule.building_id)
            current_level = existing.level if existing is not None else 0
            if target_level <= current_level:
                continue

            unlocked_at = (
                (existing.unlocked_at or current.created_at or now_iso)
                if existing is not None
                else (current.created_at or now_iso)
            )
            building_state = dict(existing.state) if existing is not None else {}
            buildings[rule.building_id] = RefugeBuildingState(
                building_id=rule.building_id,
                level=target_level,
                unlocked_at=unlocked_at,
                state=building_state,
            )
            changed_buildings.append(rule.building_id)

            for reached_level in range(current_level + 1, target_level + 1):
                if reached_level < rule.event_from_level:
                    continue
                event_id = _event_id(rule.building_id, reached_level)
                if event_id in existing_event_ids:
                    continue
                events.append(
                    RefugeHistoricalEvent(
                        event_id=event_id,
                        event_type="building_level_reached",
                        occurred_at=now_iso,
                        data={
                            "building_id": rule.building_id,
                            "level": reached_level,
                            "metric_key": rule.metric_key,
                        },
                    )
                )
                existing_event_ids.add(event_id)

        changed = bool(changed_buildings)
        if changed:
            current = replace(
                current,
                buildings=tuple(
                    sorted(
                        buildings.values(),
                        key=lambda item: item.building_id,
                    )
                ),
                events=tuple(events),
            )
            current = await self.store.save_state(current)

        return RefugeWorldEvaluation(
            state=current,
            changed=changed,
            changed_buildings=tuple(sorted(changed_buildings)),
            render_signature=world_render_signature(current),
        )


refuge_world_service = RefugeWorldService()


__all__ = [
    "BuildingProgressionRule",
    "RefugeWorldEvaluation",
    "RefugeWorldService",
    "level_for_metric",
    "refuge_world_service",
    "world_render_signature",
]
