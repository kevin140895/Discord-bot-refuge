from __future__ import annotations

from dataclasses import dataclass

import discord


COMMUNITY_GOALS_ACCENT = discord.Colour.gold()


@dataclass(frozen=True, slots=True)
class CommunityGoalDisplay:
    """Display-ready values for one community goal.

    Progress calculation and metric formatting stay outside the UI layer.
    """

    title: str
    metric_emoji: str
    progress_value: str
    target_value: str
    percent: int
    progress_bar: str
    deadline: str
    reward_text: str | None = None


class CommunityGoalsView(discord.ui.LayoutView):
    """Read-only Components V2 dashboard for community objectives."""

    def __init__(
        self,
        *,
        active_goals: tuple[CommunityGoalDisplay, ...] = (),
        completed_titles: tuple[str, ...] = (),
    ) -> None:
        super().__init__(timeout=None)

        container = discord.ui.Container(accent_colour=COMMUNITY_GOALS_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🎯 OBJECTIFS COMMUNAUTAIRES\n"
                "Progression collective du Refuge"
            )
        )

        if not active_goals:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "Aucun objectif communautaire actif pour le moment."
                )
            )
        else:
            for goal in active_goals:
                container.add_item(discord.ui.Separator())
                lines = [
                    f"### {goal.title}",
                    f"`{goal.progress_bar}` **{goal.percent}%**",
                    (
                        f"{goal.metric_emoji} **{goal.progress_value}** / "
                        f"**{goal.target_value}**"
                    ),
                    f"⏳ Fin {goal.deadline}",
                ]
                if goal.reward_text:
                    lines.append(f"🎁 Récompense prévue : **{goal.reward_text}**")
                container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        if completed_titles:
            container.add_item(discord.ui.Separator())
            recent_lines = ["### ✅ Derniers objectifs réussis"]
            recent_lines.extend(f"• {title}" for title in completed_titles)
            container.add_item(discord.ui.TextDisplay("\n".join(recent_lines)))

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Progression calculée depuis la création de chaque objectif"
            )
        )
        self.add_item(container)


__all__ = [
    "COMMUNITY_GOALS_ACCENT",
    "CommunityGoalDisplay",
    "CommunityGoalsView",
]
