from __future__ import annotations

from dataclasses import dataclass

import discord


SEASON_LEADERBOARD_ACCENT = discord.Colour.gold()


@dataclass(frozen=True, slots=True)
class SeasonLeaderboardEntry:
    """Display-ready values for one seasonal leaderboard row."""

    rank: int
    display_name: str
    value: str


class SeasonLeaderboardView(discord.ui.LayoutView):
    """Read-only mobile-first Components V2 seasonal leaderboard."""

    def __init__(
        self,
        *,
        metric_label: str,
        season_label_text: str,
        entries: tuple[SeasonLeaderboardEntry, ...],
        tracking_note: str | None = None,
    ) -> None:
        super().__init__(timeout=None)

        container = discord.ui.Container(accent_colour=SEASON_LEADERBOARD_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🏆 CLASSEMENT SAISONNIER\n"
                f"**{metric_label} · {season_label_text}**"
            )
        )
        container.add_item(discord.ui.Separator())

        if entries:
            lines: list[str] = []
            for entry in entries:
                rank_label = {
                    1: "🥇",
                    2: "🥈",
                    3: "🥉",
                }.get(entry.rank, f"**#{entry.rank}**")
                lines.append(
                    f"{rank_label} {entry.display_name} — **{entry.value}**"
                )
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))
        else:
            container.add_item(
                discord.ui.TextDisplay(
                    "Aucune activité enregistrée dans cette catégorie."
                )
            )

        if tracking_note:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# {tracking_note}"))

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Saisons mensuelles · Europe/Paris · historique conservé"
            )
        )
        self.add_item(container)


__all__ = [
    "SEASON_LEADERBOARD_ACCENT",
    "SeasonLeaderboardEntry",
    "SeasonLeaderboardView",
]
