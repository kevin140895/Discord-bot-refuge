from __future__ import annotations

from dataclasses import dataclass

import discord


ACHIEVEMENTS_ACCENT = discord.Colour.gold()


@dataclass(frozen=True, slots=True)
class AchievementDisplay:
    """Display-ready values for one achievement entry."""

    status: str
    emoji: str
    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class AchievementCategoryDisplay:
    """Display-ready values for one achievement category."""

    label: str
    achievements: tuple[AchievementDisplay, ...]


class AchievementsView(discord.ui.LayoutView):
    """Read-only mobile-first Components V2 achievements panel."""

    def __init__(
        self,
        *,
        display_name: str,
        unlocked_count: int,
        total_count: int,
        categories: tuple[AchievementCategoryDisplay, ...],
        newly_unlocked_count: int = 0,
    ) -> None:
        super().__init__(timeout=None)

        container = discord.ui.Container(accent_colour=ACHIEVEMENTS_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🏅 SUCCÈS DU REFUGE\n"
                f"**{display_name}**"
            )
        )

        container.add_item(discord.ui.Separator())
        summary = f"**{unlocked_count}/{total_count}** badges débloqués."
        if newly_unlocked_count:
            summary += (
                "\n"
                f"✨ **{newly_unlocked_count} nouveau(x) succès** reconnu(s) maintenant."
            )
        container.add_item(discord.ui.TextDisplay(summary))

        for category in categories:
            if not category.achievements:
                continue
            lines = [f"### {category.label}"]
            lines.extend(
                f"{achievement.status} {achievement.emoji} "
                f"**{achievement.name}** — {achievement.detail}"
                for achievement in category.achievements
            )
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        self.add_item(container)


__all__ = [
    "ACHIEVEMENTS_ACCENT",
    "AchievementCategoryDisplay",
    "AchievementDisplay",
    "AchievementsView",
]
