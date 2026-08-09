from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.seasonal_leaderboards as seasonal
from ui.season_leaderboard_view import (
    SeasonLeaderboardEntry,
    SeasonLeaderboardView,
)


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def test_season_leaderboard_view_renders_mobile_read_only_top() -> None:
    view = SeasonLeaderboardView(
        metric_label="XP gagnée",
        season_label_text="Août 2026",
        entries=(
            SeasonLeaderboardEntry(rank=1, display_name="Alice", value="120 XP"),
            SeasonLeaderboardEntry(rank=2, display_name="Bob", value="90 XP"),
            SeasonLeaderboardEntry(rank=4, display_name="Cara", value="40 XP"),
        ),
        tracking_note="Suivi de cette saison depuis <t:1785542400:d>.",
    )

    text = _text(view)
    assert isinstance(view, discord.ui.LayoutView)
    assert "🏆 CLASSEMENT SAISONNIER" in text
    assert "**XP gagnée · Août 2026**" in text
    assert "🥇 Alice — **120 XP**" in text
    assert "🥈 Bob — **90 XP**" in text
    assert "**#4** Cara — **40 XP**" in text
    assert "Suivi de cette saison depuis <t:1785542400:d>." in text
    assert "Saisons mensuelles · Europe/Paris · historique conservé" in text
    assert _buttons(view) == []


def test_season_leaderboard_view_handles_empty_category() -> None:
    view = SeasonLeaderboardView(
        metric_label="Messages",
        season_label_text="Août 2026",
        entries=(),
    )

    assert "Aucune activité enregistrée dans cette catégorie." in _text(view)


@pytest.mark.asyncio
async def test_classement_saison_keeps_ranking_filters_and_sends_v2(monkeypatch) -> None:
    get_season = AsyncMock(
        return_value={
            "started_at": "2026-08-01T00:00:00+02:00",
            "users": {
                "99": {"xp_earned": 999},
                "1": {"xp_earned": 120},
                "2": {"xp_earned": 90},
                "3": {"xp_earned": 40},
            },
        }
    )
    monkeypatch.setattr(seasonal.season_store, "get_season", get_season)

    members = {
        99: SimpleNamespace(display_name="RankingBot", bot=True),
        1: SimpleNamespace(display_name="Alice", bot=False),
        2: SimpleNamespace(display_name="Bob", bot=False),
        3: SimpleNamespace(display_name="Cara", bot=False),
    }
    guild = SimpleNamespace(get_member=lambda user_id: members.get(user_id))
    send_message = AsyncMock()
    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(send_message=send_message),
    )
    categorie = SimpleNamespace(value="xp")
    cog = object.__new__(seasonal.SeasonalLeaderboardsCog)

    await seasonal.SeasonalLeaderboardsCog.classement_saison.callback(
        cog,
        interaction,
        categorie,
        "2026-08",
    )

    get_season.assert_awaited_once_with("2026-08")
    send_message.assert_awaited_once()
    kwargs = send_message.await_args.kwargs
    assert "embed" not in kwargs
    assert isinstance(kwargs["view"], SeasonLeaderboardView)

    text = _text(kwargs["view"])
    assert "**XP gagnée · Août 2026**" in text
    assert "RankingBot" not in text
    assert "🥇 Alice — **120 XP**" in text
    assert "🥈 Bob — **90 XP**" in text
    assert "🥉 Cara — **40 XP**" in text
    assert "Suivi de cette saison depuis" in text
