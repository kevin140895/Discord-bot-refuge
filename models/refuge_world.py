from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

REFUGE_WORLD_SCHEMA_VERSION = 1


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict_copy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class RefugeBuildingState:
    building_id: str
    level: int = 0
    unlocked_at: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "level": max(0, int(self.level)),
            "unlocked_at": self.unlocked_at,
            "state": dict(self.state),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        fallback_id: str | None = None,
    ) -> "RefugeBuildingState":
        building_id = _string_or_none(payload.get("building_id")) or fallback_id
        if not building_id:
            raise ValueError("building_id is required")
        try:
            level = max(0, int(payload.get("level", 0)))
        except (TypeError, ValueError):
            level = 0
        return cls(
            building_id=building_id,
            level=level,
            unlocked_at=_string_or_none(payload.get("unlocked_at")),
            state=_dict_copy(payload.get("state")),
        )


@dataclass(frozen=True, slots=True)
class RefugeHistoricalEvent:
    event_id: str
    event_type: str
    occurred_at: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RefugeHistoricalEvent":
        event_id = _string_or_none(payload.get("event_id"))
        event_type = _string_or_none(payload.get("event_type"))
        occurred_at = _string_or_none(payload.get("occurred_at"))
        if not event_id or not event_type or not occurred_at:
            raise ValueError("historical event requires id, type and occurred_at")
        return cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            data=_dict_copy(payload.get("data")),
        )


@dataclass(frozen=True, slots=True)
class RefugePanelState:
    channel_id: int | None = None
    message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RefugePanelState":
        def _positive_int_or_none(value: Any) -> int | None:
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                return None
            return normalized if normalized > 0 else None

        return cls(
            channel_id=_positive_int_or_none(payload.get("channel_id")),
            message_id=_positive_int_or_none(payload.get("message_id")),
        )


@dataclass(frozen=True, slots=True)
class RefugeConstructionState:
    construction_id: str
    status: str
    project_id: str | None = None
    opened_at: str | None = None
    closes_at: str | None = None
    started_at: str | None = None
    completes_at: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "construction_id": self.construction_id,
            "status": self.status,
            "project_id": self.project_id,
            "opened_at": self.opened_at,
            "closes_at": self.closes_at,
            "started_at": self.started_at,
            "completes_at": self.completes_at,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RefugeConstructionState":
        construction_id = _string_or_none(payload.get("construction_id"))
        status = _string_or_none(payload.get("status"))
        if not construction_id or not status:
            raise ValueError("construction requires construction_id and status")
        return cls(
            construction_id=construction_id,
            status=status,
            project_id=_string_or_none(payload.get("project_id")),
            opened_at=_string_or_none(payload.get("opened_at")),
            closes_at=_string_or_none(payload.get("closes_at")),
            started_at=_string_or_none(payload.get("started_at")),
            completes_at=_string_or_none(payload.get("completes_at")),
            data=_dict_copy(payload.get("data")),
        )


@dataclass(frozen=True, slots=True)
class RefugeWorldSnapshot:
    season_id: str
    captured_at: str
    buildings: tuple[RefugeBuildingState, ...] = ()
    event_ids: tuple[str, ...] = ()
    active_construction: RefugeConstructionState | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "season_id": self.season_id,
            "captured_at": self.captured_at,
            "buildings": {
                building.building_id: building.to_dict()
                for building in self.buildings
            },
            "event_ids": list(self.event_ids),
            "active_construction": (
                self.active_construction.to_dict()
                if self.active_construction is not None
                else None
            ),
            "state": dict(self.state),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        fallback_season_id: str | None = None,
    ) -> "RefugeWorldSnapshot":
        season_id = _string_or_none(payload.get("season_id")) or fallback_season_id
        captured_at = _string_or_none(payload.get("captured_at"))
        if not season_id or not captured_at:
            raise ValueError("snapshot requires season_id and captured_at")
        buildings = _parse_buildings(payload.get("buildings"))
        raw_event_ids = payload.get("event_ids", [])
        event_ids = (
            tuple(
                event_id
                for raw in raw_event_ids
                if (event_id := _string_or_none(raw))
            )
            if isinstance(raw_event_ids, list)
            else ()
        )
        raw_construction = payload.get("active_construction")
        active_construction = (
            RefugeConstructionState.from_dict(raw_construction)
            if isinstance(raw_construction, Mapping)
            else None
        )
        return cls(
            season_id=season_id,
            captured_at=captured_at,
            buildings=buildings,
            event_ids=event_ids,
            active_construction=active_construction,
            state=_dict_copy(payload.get("state")),
        )


def _parse_buildings(value: Any) -> tuple[RefugeBuildingState, ...]:
    if not isinstance(value, Mapping):
        return ()
    buildings: list[RefugeBuildingState] = []
    for raw_id, raw_payload in value.items():
        if not isinstance(raw_payload, Mapping):
            continue
        try:
            buildings.append(
                RefugeBuildingState.from_dict(
                    raw_payload,
                    fallback_id=str(raw_id),
                )
            )
        except ValueError:
            continue
    return tuple(sorted(buildings, key=lambda building: building.building_id))


@dataclass(frozen=True, slots=True)
class RefugeWorldState:
    schema_version: int = REFUGE_WORLD_SCHEMA_VERSION
    created_at: str | None = None
    buildings: tuple[RefugeBuildingState, ...] = ()
    events: tuple[RefugeHistoricalEvent, ...] = ()
    snapshots: tuple[RefugeWorldSnapshot, ...] = ()
    panel: RefugePanelState = field(default_factory=RefugePanelState)
    active_construction: RefugeConstructionState | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "buildings": {
                building.building_id: building.to_dict()
                for building in self.buildings
            },
            "events": [event.to_dict() for event in self.events],
            "snapshots": {
                snapshot.season_id: snapshot.to_dict()
                for snapshot in self.snapshots
            },
            "panel": self.panel.to_dict(),
            "active_construction": (
                self.active_construction.to_dict()
                if self.active_construction is not None
                else None
            ),
            "state": dict(self.state),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RefugeWorldState":
        try:
            schema_version = int(payload.get("schema_version", 0))
        except (TypeError, ValueError):
            schema_version = 0
        if schema_version != REFUGE_WORLD_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported refuge world schema version: {schema_version}"
            )

        events: list[RefugeHistoricalEvent] = []
        raw_events = payload.get("events", [])
        if isinstance(raw_events, list):
            for raw_event in raw_events:
                if not isinstance(raw_event, Mapping):
                    continue
                try:
                    events.append(RefugeHistoricalEvent.from_dict(raw_event))
                except ValueError:
                    continue

        snapshots: list[RefugeWorldSnapshot] = []
        raw_snapshots = payload.get("snapshots", {})
        if isinstance(raw_snapshots, Mapping):
            for raw_season_id, raw_snapshot in raw_snapshots.items():
                if not isinstance(raw_snapshot, Mapping):
                    continue
                try:
                    snapshots.append(
                        RefugeWorldSnapshot.from_dict(
                            raw_snapshot,
                            fallback_season_id=str(raw_season_id),
                        )
                    )
                except ValueError:
                    continue

        raw_panel = payload.get("panel")
        panel = (
            RefugePanelState.from_dict(raw_panel)
            if isinstance(raw_panel, Mapping)
            else RefugePanelState()
        )
        raw_construction = payload.get("active_construction")
        active_construction = (
            RefugeConstructionState.from_dict(raw_construction)
            if isinstance(raw_construction, Mapping)
            else None
        )

        return cls(
            schema_version=schema_version,
            created_at=_string_or_none(payload.get("created_at")),
            buildings=_parse_buildings(payload.get("buildings")),
            events=tuple(events),
            snapshots=tuple(
                sorted(snapshots, key=lambda snapshot: snapshot.season_id)
            ),
            panel=panel,
            active_construction=active_construction,
            state=_dict_copy(payload.get("state")),
        )
