from datetime import datetime, timezone

from domain.refuge_casino import RefugeCasinoConfig
from domain.refuge_fire import RefugeFireConfig
from domain.refuge_hall import RefugeHallConfig
from domain.refuge_world import RefugeWorldState, build_refuge_world_panel
from services.member_profile import MemberProfileSnapshot
from services.refuge_exploration import (
    EXPLORER_ZONE_ORDER,
    build_explorer_snapshot,
)


def _state(*, with_secret: bool = False) -> RefugeWorldState:
    return RefugeWorldState(
        generated_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        season_id="2026-S08",
        day_key="2026-08-10",
        weather="sunny",
        atmosphere="active",
        fire_level=1,
        fire_name="L’Étincelle",
        fire_intensity="Vivant",
        fire_started_at="2026-08-01T00:00:00+00:00",
        fire_seconds=0,
        hall_level=1,
        hall_name="Le Vestibule",
        hall_points=0,
        hall_activity="Calme",
        hall_started_at="2026-08-01T00:00:00+00:00",
        casino_level=1,
        casino_name="La Baraque",
        casino_points=0,
        casino_fortune="stable",
        casino_open=True,
        casino_started_at="2026-08-01T00:00:00+00:00",
        casino_jackpot_tier=0,
        casino_events=(),
        casino_secrets=("black_cat",) if with_secret else (),
        construction_phase="inactive",
        construction_project=None,
        construction_progress=0.0,
        construction_started_at=None,
        construction_ends_at=None,
        construction_options=(),
        construction_vote_counts=(),
        construction_total_votes=0,
        construction_monuments=(),
        construction_queue_size=0,
        summary_signature="summary",
        changed=False,
    )


def _panel(state: RefugeWorldState):
    return build_refuge_world_panel(state)


def test_explorer_has_exact_six_validated_zones_and_no_default_thresholds():
    snapshot = build_explorer_snapshot(
        panel=_panel(_state()),
        fire_config=RefugeFireConfig(),
        hall_config=RefugeHallConfig(),
        casino_config=RefugeCasinoConfig(),
    )

    assert tuple(zone.zone_id for zone in snapshot.zones) == EXPLORER_ZONE_ORDER
    assert "seuil non calibré" in " ".join(snapshot.get_zone("fire").details)
    assert "seuil non calibré" in " ".join(snapshot.get_zone("hall").details)
    assert "seuil non calibré" in " ".join(snapshot.get_zone("casino").details)
    assert snapshot.get_zone("construction").details[0] == "Aucun chantier actif."


def test_explorer_uses_configured_next_milestone_without_inventing_progress():
    snapshot = build_explorer_snapshot(
        panel=_panel(_state()),
        fire_config=RefugeFireConfig(
            level_thresholds_seconds=(3600, 7200, 10800, 14400)
        ),
        hall_config=RefugeHallConfig(level_thresholds_points=(10, 20, 30, 40)),
        casino_config=RefugeCasinoConfig(level_thresholds_points=(10, 20, 30, 40)),
    )

    fire_text = " ".join(snapshot.get_zone("fire").details)
    assert "Le Campement à 1 h 00" in fire_text
    # Dates use slashes (e.g. 01/08/2026); only forbid an invented x / y ratio.
    assert " / " not in fire_text


def test_mysteries_reveal_only_discovered_secret_names():
    snapshot = build_explorer_snapshot(
        panel=_panel(_state(with_secret=True)),
        fire_config=RefugeFireConfig(),
        hall_config=RefugeHallConfig(),
        casino_config=RefugeCasinoConfig(),
    )

    text = " ".join(snapshot.get_zone("mysteries").history)
    assert "La Nuit des Étoiles" in text
    assert "Le Premier Visiteur" not in text
    assert "Le Cercle complet" not in text


def test_footprint_ignores_rank_fields_and_keeps_only_personal_history():
    profile = MemberProfileSnapshot(
        user_id=42,
        xp=1250,
        level=7,
        achievements_unlocked=2,
        achievements_total=9,
        messages=123,
        voice_seconds=456,
        casino_bets=8,
        casino_wagered=90,
        casino_winnings=120,
        casino_net=30,
        top_roles=("Gardien", "Explorateur"),
    )
    snapshot = build_explorer_snapshot(
        panel=_panel(_state()),
        fire_config=RefugeFireConfig(),
        hall_config=RefugeHallConfig(),
        casino_config=RefugeCasinoConfig(),
        member_profile=profile,
    )

    footprint = snapshot.get_zone("footprint")
    text = " ".join(footprint.details + footprint.history)
    assert "Niveau 7" in text
    assert "2 / 9 succès" in text
    assert "123 messages" in text
    assert "7 min 36 s en vocal" in text
    assert "+30 XP net" in text
    assert "Gardien" in text
    assert "Explorateur" in text
    assert "rang" not in text.lower()
