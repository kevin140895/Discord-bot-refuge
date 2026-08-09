from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import discord


@dataclass(frozen=True, slots=True)
class CasinoLeaderboardEntry:
    rank: int
    display_name: str
    net: int
    winnings: int
    wagered: int
    bets: int


class CasinoLeaderboardView(discord.ui.LayoutView):
    """Read-only Components V2 leaderboard for casino performance."""

    def __init__(self, entries: Iterable[CasinoLeaderboardEntry]) -> None:
        super().__init__(timeout=None)
        rows = tuple(entries)

        container = discord.ui.Container(accent_colour=discord.Colour.gold())
        container.add_item(discord.ui.TextDisplay("## 🏆 Top Casino"))

        if not rows:
            container.add_item(
                discord.ui.TextDisplay(
                    "Aucune activité casino enregistrée pour le moment."
                )
            )
        else:
            container.add_item(
                discord.ui.TextDisplay(
                    "Classement par **résultat net** : gains - mises."
                )
            )
            container.add_item(discord.ui.Separator())

            for index, entry in enumerate(rows):
                bet_label = "pari" if entry.bets == 1 else "paris"
                container.add_item(
                    discord.ui.TextDisplay(
                        f"**#{entry.rank} · {entry.display_name}**\n"
                        f"**{entry.net:+d} XP net** · {entry.bets} {bet_label}\n"
                        f"{entry.wagered} XP misés · {entry.winnings} XP gagnés"
                    )
                )
                if index < len(rows) - 1:
                    container.add_item(discord.ui.Separator())

        self.add_item(container)


__all__ = ["CasinoLeaderboardEntry", "CasinoLeaderboardView"]
