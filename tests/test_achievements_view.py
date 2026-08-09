from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.achievements as achievements
from ui.achievements_view import (
    AchievementCategoryDisplay,
    AchievementDisplay,
    AchievementsView,
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


def test_achievements_view_renders_read_only_components_v2_panel() -> None:
    view = AchievementsView(
        display_name="Kevin",
        unlocked_count=2,
        total_count=9,
        newly_unlocked_count=1,
        categories=(
            AchievementCategoryDisplay(
                label="Progression XP",
                achievements=(
                    AchievementDisplay(
                        status="✅",
                        emoji="🥉",
                        name="Membre Bronze",
                        detail="Atteindre le niveau 5.",
                    ),
                    AchievementDisplay(
                        status="🔒",
                        emoji="🥈",
                        name="Membre Argent",
                        detail="niveau 7/10",
                    ),
                ),
            ),
        ),
    )

    text = _text(view)
    assert isinstance(view, discord.ui.LayoutView)
    assert "🏅 SUCCÈS DU REFUGE" in text
    assert "**Kevin**" in text
    assert "**2/9** badges débloqués." in text
    assert "**1 nouveau(x) succès**" in text
    assert "### Progression XP" in text
    assert "✅ 🥉 **Membre Bronze** — Atteindre le niveau 5." in text
    assert "🔒 🥈 **Membre Argent** — niveau 7/10" in text
    assert _buttons(view) == []


@pytest.mark.asyncio
async def test_succes_command_keeps_existing_sync_and_sends_v2_view(monkeypatch) -> None:
    cog = object.__new__(achievements.AchievementsCog)
    cog._sync_member = AsyncMock(
        return_value=(
            {
                "level": 7,
                "casino_bets": 0,
                "tenure_days": 40,
            },
            ["level_5"],
        )
    )
    get_user_achievements = AsyncMock(
        return_value={
            "level_5": "2026-08-09T02:00:00+00:00",
            "tenure_30_days": "2026-08-09T02:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        achievements.achievement_store,
        "get_user_achievements",
        get_user_achievements,
    )

    target = SimpleNamespace(id=42, display_name="Kevin")
    send_message = AsyncMock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=send_message),
    )

    await achievements.AchievementsCog.succes.callback(
        cog,
        interaction,
        target,
    )

    cog._sync_member.assert_awaited_once_with(target)
    get_user_achievements.assert_awaited_once_with(42)
    send_message.assert_awaited_once()

    kwargs = send_message.await_args.kwargs
    assert "embed" not in kwargs
    assert isinstance(kwargs["view"], AchievementsView)

    text = _text(kwargs["view"])
    assert "**2/9** badges débloqués." in text
    assert "✅ 🥉 **Membre Bronze** — Atteindre le niveau 5." in text
    assert "🔒 🥈 **Membre Argent** — niveau 7/10" in text
    assert "✅ 🌱 **Un mois au Refuge** — Être membre du Refuge depuis 30 jours." in text
