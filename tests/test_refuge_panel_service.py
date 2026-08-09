from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from models.refuge_world import (
    RefugeConstructionState,
    RefugeHistoricalEvent,
    RefugeWorldState,
)
from services.refuge_panel import (
    RefugePanelService,
    construction_label,
    event_label,
    latest_event,
)


class _Service:
    def __init__(self, status):
        self.status = status
        self.calls = []

    async def evaluate(self, *, at=None):
        self.calls.append(at)
        return self.status


class _Renderer:
    def __init__(self):
        self.calls = []

    async def render_png_async(self, state, *, context=None):
        self.calls.append((state, context))
        return b"PNG"


def _status(state, **kwargs):
    defaults = {
        "state": state,
        "level": 1,
        "level_name": "Niveau I",
        "changed": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_panel_service_evaluates_systems_sequentially_and_builds_snapshot():
    at = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)
    base = RefugeWorldState(created_at="2026-08-09T00:00:00+00:00")
    fire_state = replace(base, state={"stage": "fire"})
    hall_state = replace(base, state={"stage": "hall"})
    final_state = replace(base, state={"stage": "casino"})

    fire = _Service(
        _status(
            fire_state,
            level=2,
            level_name="Le Campement",
            intensity="normal",
            changed=True,
        )
    )
    hall = _Service(
        _status(
            hall_state,
            level=3,
            level_name="Hall des Légendes",
        )
    )
    casino = _Service(
        _status(
            final_state,
            level=1,
            level_name="Baraque de Jeux",
            fortune="stable",
            fortune_name="Stable",
            is_open=True,
        )
    )
    renderer = _Renderer()
    service = RefugePanelService(
        fire_service=fire,
        hall_service=hall,
        casino_service=casino,
        renderer=renderer,
    )

    snapshot = await service.evaluate(at=at)

    assert snapshot.state == final_state
    assert snapshot.season_id == "2026-08"
    assert snapshot.season_label == "Août 2026"
    assert snapshot.fire_level == 2
    assert snapshot.fire_intensity_name == "Vivant"
    assert snapshot.hall_level == 3
    assert snapshot.casino_fortune_name == "Stable"
    assert snapshot.casino_is_open is True
    assert snapshot.construction_label == "Aucun chantier actif"
    assert snapshot.changed is True
    assert len(fire.calls) == len(hall.calls) == len(casino.calls) == 1
    assert fire.calls[0] == hall.calls[0] == casino.calls[0]

    assert await service.render_png(snapshot) == b"PNG"
    assert renderer.calls[0][0] == final_state
    assert renderer.calls[0][1] == snapshot.context


def test_construction_label_prefers_persisted_project_name():
    state = RefugeWorldState(
        active_construction=RefugeConstructionState(
            construction_id="build-1",
            status="building",
            project_id="observatory",
            data={"project_name": "Observatoire"},
        )
    )
    assert construction_label(state) == "Construction en cours · Observatoire"


def test_latest_event_and_labels_are_deterministic():
    early = RefugeHistoricalEvent(
        event_id="first",
        event_type="hall_gallery_marker",
        occurred_at="2026-08-09T10:00:00+00:00",
    )
    late = RefugeHistoricalEvent(
        event_id="second",
        event_type="casino_jackpot_observed",
        occurred_at="2026-08-09T11:00:00+00:00",
        data={"tier": 1000},
    )
    state = RefugeWorldState(events=(late, early))
    selected = latest_event(state)
    assert selected == late
    assert event_label(selected) == "Jackpot machine à sous · 1000 XP"


def test_explicit_event_name_wins_over_generic_copy():
    event = RefugeHistoricalEvent(
        event_id="secret",
        event_type="casino_secret_discovered",
        occurred_at="2026-08-09T11:00:00+00:00",
        data={"name": "Le Chat Noir"},
    )
    assert event_label(event) == "Le Chat Noir"
