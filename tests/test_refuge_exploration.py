from __future__ import annotations

from models.refuge_world import (
    RefugeBuildingState,
    RefugeHistoricalEvent,
    RefugeWorldState,
)
from rendering.refuge_world import RefugeRenderContext
from services.member_profile import MemberProfileSnapshot
from services.refuge_casino import RefugeCasinoConfig
from services.refuge_exploration import (
    EXPLORER_ZONE_ORDER,
    build_explorer_snapshot,
    build_footprint_snapshot,
)
from services.refuge_fire import RefugeFireConfig
from services.refuge_hall import RefugeHallConfig
from services.refuge_panel import RefugePanelSnapshot


def _state(*, with_secret: bool = False) -> RefugeWorldState:
    events = []
    if with_secret:
        events.append(
            RefugeHistoricalEvent(
                event_id="fire:secret:night_of_stars",
                event_type="fire_secret_discovered",
                occurred_at="2026-08-09T12:00:00+00:00",
                data={
                    "building_id": "fire",
                    "secret_id": "night_of_stars",
                    "name": "La Nuit des Étoiles",
                },
            )
        )
    return RefugeWorldState(
        created_at="2026-08-01T00:00:00+00:00",
        buildings=(
            RefugeBuildingState(
                building_id="fire",
                level=1,
                unlocked_at="2026-08-01T00:00:00+00:00",
            ),
            RefugeBuildingState(
                building_id="hall",
                level=1,
                unlocked_at="2026-08-01T00:00:00+00:00",
            ),
            RefugeBuildingState(
                building_id="casino",
                level=1,
                unlocked_at="2026-08-01T00:00:00+00:00",
            ),
        ),
        events=tuple(events),
    )


def _panel(state: RefugeWorldState) -> RefugePanelSnapshot:
    return RefugePanelSnapshot(
        state=state,
        context=RefugeRenderContext(season="summer", daypart="day"),
        season_id="2026-08",
        season_label="Août 2026",
        fire_level=1,
        fire_name="L’Étincelle",
        fire_intensity="normal",
        fire_intensity_name="Vivant",
        hall_level=1,
        hall_name="Cabane des Souvenirs",
        casino_level=1,
        casino_name="Baraque de Jeux",
        casino_fortune="stable",
        casino_fortune_name="Stable",
        casino_is_open=True,
        construction_label="Aucun chantier actif",
        latest_event_id=None,
        latest_event_label=None,
        visual_signature="visual",
        summary_signature="summary",
        changed=False,
    )


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
    assert "/" not in fire_text


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
        achievement_ids=("casino_1_bet", "level_5"),
        season_id="2026-08",
        season_xp=310,
        season_xp_rank=1,
        season_messages=54,
        season_messages_rank=1,
        season_voice_seconds=7200,
        season_voice_rank=1,
        season_casino_net=-25,
        season_casino_rank=1,
        casino_bets=12,
        casino_wagered=500,
        casino_winnings=450,
        casino_net=-50,
    )
    state = RefugeWorldState(
        buildings=(
            RefugeBuildingState(
                building_id="hall",
                level=1,
                state={
                    "historical_firsts": [
                        {
                            "achievement_id": "level_5",
                            "user_id": 42,
                            "unlocked_at": "2026-08-03T12:00:00+00:00",
                        }
                    ]
                },
            ),
        ),
        events=(
            RefugeHistoricalEvent(
                event_id="casino:jackpot:mine",
                event_type="casino_jackpot_observed",
                occurred_at="2026-08-08T12:00:00+00:00",
                data={"building_id": "casino", "tier": 500, "user_id": 42},
            ),
            RefugeHistoricalEvent(
                event_id="casino:jackpot:other",
                event_type="casino_jackpot_observed",
                occurred_at="2026-08-09T12:00:00+00:00",
                data={"building_id": "casino", "tier": 1000, "user_id": 99},
            ),
        ),
    )

    footprint = build_footprint_snapshot(profile=profile, state=state)

    assert not hasattr(footprint, "season_xp_rank")
    assert not hasattr(footprint, "season_messages_rank")
    assert not hasattr(footprint, "season_voice_rank")
    assert not hasattr(footprint, "season_casino_rank")
    assert footprint.season_xp == 310
    assert footprint.season_messages == 54
    assert footprint.season_voice_seconds == 7200
    assert any(
        "Jackpot machine à sous · 500 XP" in trace.label
        for trace in footprint.historical_traces
    )
    assert any(
        "Première historique au Hall" in trace.label
        for trace in footprint.historical_traces
    )
    assert all("1000 XP" not in trace.label for trace in footprint.historical_traces)
    assert "🎲 Premier pari" in footprint.achievement_names
    assert "🥉 Membre Bronze" in footprint.achievement_names
