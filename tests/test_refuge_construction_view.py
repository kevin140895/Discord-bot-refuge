from __future__ import annotations

import discord

from services.refuge_construction import (
    RefugeConstructionOption,
    RefugeConstructionSnapshot,
)
from ui.refuge_construction_view import RefugeConstructionView


OPTIONS = (
    RefugeConstructionOption(
        "star_observatory",
        "Observatoire des Étoiles",
        "🔭",
        "Observer le ciel.",
    ),
    RefugeConstructionOption(
        "memory_garden",
        "Jardin des Souvenirs",
        "🌿",
        "Conserver les traces.",
    ),
    RefugeConstructionOption(
        "lantern_tower",
        "Tour des Lanternes",
        "🏮",
        "Veiller sur le Refuge.",
    ),
)


def _snapshot(**overrides):
    values = {
        "active": True,
        "status": "voting",
        "construction_id": "goal:abc",
        "source_goal_id": "abc",
        "source_goal_title": "100 heures ensemble",
        "options": OPTIONS,
        "allowed_project_ids": tuple(option.project_id for option in OPTIONS),
        "user_vote": None,
        "project_id": None,
        "project_name": None,
        "opened_at": "2026-08-09T12:00:00+00:00",
        "closes_at": "2026-08-12T12:00:00+00:00",
        "started_at": None,
        "completes_at": None,
        "progress_percent": 0,
        "winner_method": None,
        "final_results": (),
        "completed_monuments": (),
    }
    values.update(overrides)
    return RefugeConstructionSnapshot(**values)


def _text(view):
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _selects(view):
    return [
        item for item in view.walk_children()
        if isinstance(item, discord.ui.Select)
    ]


def test_voting_view_has_three_projects_and_hides_live_results():
    view = RefugeConstructionView(_snapshot(), owner_user_id=42)

    selects = _selects(view)
    assert len(selects) == 1
    assert [option.value for option in selects[0].options] == [
        "star_observatory",
        "memory_garden",
        "lantern_tower",
    ]
    text = _text(view)
    assert "résultats intermédiaires restent masqués" in text
    assert "Résultats finaux" not in text
    assert "100 heures ensemble" in text


def test_tie_break_view_exposes_only_tied_finalists_without_scores():
    view = RefugeConstructionView(
        _snapshot(
            status="tie_break",
            allowed_project_ids=("star_observatory", "memory_garden"),
        ),
        owner_user_id=42,
    )

    select = _selects(view)[0]
    assert {option.value for option in select.options} == {
        "star_observatory",
        "memory_garden",
    }
    text = _text(view)
    assert "Prolongation pour égalité" in text
    assert "24 h" in text
    assert "Résultats finaux" not in text


def test_building_view_reveals_final_results_and_time_progress_only():
    view = RefugeConstructionView(
        _snapshot(
            status="building",
            allowed_project_ids=(),
            project_id="memory_garden",
            project_name="Jardin des Souvenirs",
            started_at="2026-08-12T12:00:00+00:00",
            completes_at="2026-08-19T12:00:00+00:00",
            progress_percent=50,
            winner_method="random_tie",
            final_results=(
                ("lantern_tower", 1),
                ("memory_garden", 4),
                ("star_observatory", 4),
            ),
        ),
        owner_user_id=42,
    )

    assert _selects(view) == []
    text = _text(view)
    assert "50%" in text
    assert "temps écoulé" in text
    assert "tirage au sort après égalité persistante" in text
    assert "Résultats finaux" in text


def test_inactive_view_lists_existing_permanent_monuments_without_vote_control():
    view = RefugeConstructionView(
        _snapshot(
            active=False,
            status=None,
            construction_id=None,
            source_goal_id=None,
            source_goal_title=None,
            options=(),
            allowed_project_ids=(),
            project_id=None,
            project_name=None,
            opened_at=None,
            closes_at=None,
            started_at=None,
            completes_at=None,
            completed_monuments=("Observatoire des Étoiles",),
        ),
        owner_user_id=42,
    )

    assert _selects(view) == []
    text = _text(view)
    assert "Aucun chantier actif" in text
    assert "Observatoire des Étoiles" in text
