from __future__ import annotations

import discord

from services.member_profile import MemberProfileSnapshot
from utils.seasons import season_label


PROFILE_ACCENT = discord.Colour.blurple()


def _format_rank(rank: int | None) -> str:
    return f"#{rank}" if rank is not None else "—"


def _format_voice(seconds: int) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def _format_xp(value: int) -> str:
    return f"{int(value):,}".replace(",", " ") + " XP"


def _format_signed_xp(value: int) -> str:
    return f"{int(value):+d} XP"


class ProfileView(discord.ui.LayoutView):
    """Read-only Components V2 overview for one Refuge member.

    Navigation buttons are intentionally disabled in the first iteration.
    They become interactive in the dedicated navigation step of the rollout.
    """

    def __init__(
        self,
        snapshot: MemberProfileSnapshot,
        *,
        display_name: str,
        avatar_url: str | None = None,
    ) -> None:
        super().__init__(timeout=180)

        container = discord.ui.Container(accent_colour=PROFILE_ACCENT)

        identity = discord.ui.TextDisplay(
            "## 🏆 PROFIL DU REFUGE\n"
            f"**{display_name}**\n"
            f"Niveau **{snapshot.level}** · **{_format_xp(snapshot.xp)}**"
        )
        if avatar_url:
            container.add_item(
                discord.ui.Section(
                    identity,
                    accessory=discord.ui.Thumbnail(
                        avatar_url,
                        description=f"Avatar de {display_name}",
                    ),
                )
            )
        else:
            container.add_item(identity)

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"### 📊 {season_label(snapshot.season_id)}\n"
                f"⚡ XP gagnée : **{_format_xp(snapshot.season_xp)}** · **{_format_rank(snapshot.season_xp_rank)}**\n"
                f"💬 Messages : **{snapshot.season_messages}** · **{_format_rank(snapshot.season_messages_rank)}**\n"
                f"🎧 Vocal : **{_format_voice(snapshot.season_voice_seconds)}** · **{_format_rank(snapshot.season_voice_rank)}**\n"
                f"🎰 Casino net : **{_format_signed_xp(snapshot.season_casino_net)}** · **{_format_rank(snapshot.season_casino_rank)}**"
            )
        )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🏅 Succès\n"
                f"**{snapshot.achievements_unlocked}/{snapshot.achievements_total}** badges débloqués"
            )
        )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🎰 Casino\n"
                f"**{snapshot.casino_bets}** paris · résultat global **{_format_signed_xp(snapshot.casino_net)}**"
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="Succès",
                    emoji="🏅",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                ),
                discord.ui.Button(
                    label="Saison",
                    emoji="📊",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                ),
                discord.ui.Button(
                    label="Casino",
                    emoji="🎰",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                ),
            )
        )

        self.add_item(container)


__all__ = [
    "PROFILE_ACCENT",
    "ProfileView",
    "_format_rank",
    "_format_signed_xp",
    "_format_voice",
    "_format_xp",
]
