from __future__ import annotations

import discord

from services.refuge_exploration import (
    RefugeExplorerSnapshot,
    RefugeExplorerZoneSnapshot,
    RefugeFootprintSnapshot,
    RefugeFootprintTrace,
)
from ui.refuge_exploration_view import (
    RefugeExplorerSelect,
    RefugeExplorerView,
    RefugeFootprintView,
)


def _explorer_snapshot() -> RefugeExplorerSnapshot:
    zones = (
        RefugeExplorerZoneSnapshot("fire", "Le Feu", "🔥", ("Feu vivant.",)),
        RefugeExplorerZoneSnapshot("hall", "Le Hall", "🏆", ("Hall calme.",)),
        RefugeExplorerZoneSnapshot("casino", "Le Casino", "🎰", ("Casino stable.",)),
        RefugeExplorerZoneSnapshot("construction", "Le Chantier", "🏗️", ("Aucun chantier actif.",)),
        RefugeExplorerZoneSnapshot("monuments", "Les Monuments", "🗿", ("Aucun monument.",)),
        RefugeExplorerZoneSnapshot(
            "mysteries",
            "Les Mystères",
            "🌌",
            ("Un mystère révélé.",),
            ("09/08/2026 · La Nuit des Étoiles",),
        ),
    )
    return RefugeExplorerSnapshot(zones=zones)


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_explorer_is_private_sized_v2_with_one_six_option_select():
    view = RefugeExplorerView(_explorer_snapshot(), owner_user_id=42)
    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout == 300
    selects = [
        item for item in view.walk_children() if isinstance(item, RefugeExplorerSelect)
    ]
    assert len(selects) == 1
    assert [option.value for option in selects[0].options] == [
        "fire",
        "hall",
        "casino",
        "construction",
        "monuments",
        "mysteries",
    ]
    assert "Le Feu" in _text(view)


def test_explorer_switches_zone_without_creating_another_surface():
    view = RefugeExplorerView(_explorer_snapshot(), owner_user_id=42)
    view.show_zone("mysteries")
    text = _text(view)
    assert "Les Mystères" in text
    assert "La Nuit des Étoiles" in text
    assert "conditions restent invisibles" in text
    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]
    assert len(selects) == 1


def test_footprint_displays_activity_without_rank_or_importance_score():
    snapshot = RefugeFootprintSnapshot(
        user_id=42,
        season_id="2026-08",
        season_label="Août 2026",
        level=7,
        xp=1250,
        season_xp=310,
        season_messages=54,
        season_voice_seconds=7200,
        season_casino_net=-25,
        achievements_unlocked=2,
        achievements_total=9,
        achievement_names=("🥉 Membre Bronze", "🎲 Premier pari"),
        casino_bets=12,
        casino_net=-50,
        historical_traces=(
            RefugeFootprintTrace(
                occurred_at="2026-08-08T12:00:00+00:00",
                label="Jackpot machine à sous · 500 XP",
            ),
        ),
    )
    view = RefugeFootprintView(snapshot, display_name="Kevin")
    text = _text(view)
    assert "MON EMPREINTE" in text
    assert "Août 2026" in text
    assert "54" in text
    assert "2 h 00" in text
    assert "Jackpot machine à sous · 500 XP" in text
    assert "#1" not in text
    assert "score d’importance" not in text.lower()
    assert "aucune comparaison d’importance" in text.lower()
