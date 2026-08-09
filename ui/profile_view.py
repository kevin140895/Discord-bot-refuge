from __future__ import annotations

from typing import Literal

import discord

from services.member_profile import MemberProfileSnapshot
from utils.achievements import ACHIEVEMENTS, CATEGORY_LABELS
from utils.seasons import season_label


PROFILE_ACCENT = discord.Colour.blurple()
ProfilePage = Literal["overview", "achievements", "season", "casino"]


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


class ProfileNavigationButton(discord.ui.Button):
    """Button that switches one ProfileView page in-place."""

    def __init__(
        self,
        *,
        page: ProfilePage,
        label: str,
        emoji: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"refuge_profile:{page}",
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileView):
            return
        view.show_page(self.page)
        await interaction.response.edit_message(view=view)


class ProfileView(discord.ui.LayoutView):
    """Interactive Components V2 profile for one Refuge member."""

    def __init__(
        self,
        snapshot: MemberProfileSnapshot,
        *,
        display_name: str,
        avatar_url: str | None = None,
        owner_user_id: int | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.snapshot = snapshot
        self.display_name = display_name
        self.avatar_url = avatar_url
        self.owner_user_id = int(owner_user_id or snapshot.user_id)
        self.current_page: ProfilePage = "overview"
        self.show_page("overview")

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message(
            "Ce panneau est contrôlé par la personne qui a lancé `/profil`.",
            ephemeral=True,
        )
        return False

    def show_page(self, page: ProfilePage) -> None:
        builders = {
            "overview": self._build_overview,
            "achievements": self._build_achievements,
            "season": self._build_season,
            "casino": self._build_casino,
        }
        builder = builders.get(page)
        if builder is None:
            raise ValueError(f"unknown profile page: {page}")
        self.current_page = page
        self.clear_items()
        self.add_item(builder())

    def _identity_item(self, title: str) -> discord.ui.Item:
        text = discord.ui.TextDisplay(
            f"## {title}\n"
            f"**{self.display_name}**\n"
            f"Niveau **{self.snapshot.level}** · **{_format_xp(self.snapshot.xp)}**"
        )
        if not self.avatar_url:
            return text
        return discord.ui.Section(
            text,
            accessory=discord.ui.Thumbnail(
                self.avatar_url,
                description=f"Avatar de {self.display_name}",
            ),
        )

    def _container(self, title: str) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=PROFILE_ACCENT)
        container.add_item(self._identity_item(title))
        return container

    def _back_row(self) -> discord.ui.ActionRow:
        return discord.ui.ActionRow(
            ProfileNavigationButton(
                page="overview",
                label="Retour au profil",
                emoji="↩️",
                style=discord.ButtonStyle.primary,
            )
        )

    def _build_overview(self) -> discord.ui.Container:
        snapshot = self.snapshot
        container = self._container("🏆 PROFIL DU REFUGE")
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
                ProfileNavigationButton(page="achievements", label="Succès", emoji="🏅"),
                ProfileNavigationButton(page="season", label="Saison", emoji="📊"),
                ProfileNavigationButton(page="casino", label="Casino", emoji="🎰"),
            )
        )
        return container

    def _build_achievements(self) -> discord.ui.Container:
        container = self._container("🏅 SUCCÈS")
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"**{self.snapshot.achievements_unlocked}/{self.snapshot.achievements_total}** badges débloqués"
            )
        )
        unlocked = set(self.snapshot.achievement_ids)
        for category, label in CATEGORY_LABELS.items():
            lines = []
            for achievement in ACHIEVEMENTS:
                if achievement.category != category:
                    continue
                status = "✅" if achievement.id in unlocked else "🔒"
                lines.append(f"{status} {achievement.emoji} **{achievement.name}**")
            if lines:
                container.add_item(discord.ui.Separator())
                container.add_item(
                    discord.ui.TextDisplay(f"### {label}\n" + "\n".join(lines))
                )
        container.add_item(self._back_row())
        return container

    def _build_season(self) -> discord.ui.Container:
        snapshot = self.snapshot
        container = self._container(f"📊 SAISON · {season_label(snapshot.season_id)}")
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"⚡ **XP gagnée**\n{_format_xp(snapshot.season_xp)} · rang **{_format_rank(snapshot.season_xp_rank)}**\n\n"
                f"💬 **Messages**\n{snapshot.season_messages} · rang **{_format_rank(snapshot.season_messages_rank)}**\n\n"
                f"🎧 **Temps vocal**\n{_format_voice(snapshot.season_voice_seconds)} · rang **{_format_rank(snapshot.season_voice_rank)}**\n\n"
                f"🎰 **Casino net**\n{_format_signed_xp(snapshot.season_casino_net)} · rang **{_format_rank(snapshot.season_casino_rank)}**"
            )
        )
        container.add_item(self._back_row())
        return container

    def _build_casino(self) -> discord.ui.Container:
        snapshot = self.snapshot
        container = self._container("🎰 CASINO")
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### Bilan global\n"
                f"Paris : **{snapshot.casino_bets}**\n"
                f"Mises : **{_format_xp(snapshot.casino_wagered)}**\n"
                f"Gains : **{_format_xp(snapshot.casino_winnings)}**\n"
                f"Résultat net : **{_format_signed_xp(snapshot.casino_net)}**"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"### {season_label(snapshot.season_id)}\n"
                f"Résultat net : **{_format_signed_xp(snapshot.season_casino_net)}**\n"
                f"Rang saisonnier : **{_format_rank(snapshot.season_casino_rank)}**"
            )
        )
        container.add_item(self._back_row())
        return container


__all__ = [
    "PROFILE_ACCENT",
    "ProfileNavigationButton",
    "ProfileView",
    "_format_rank",
    "_format_signed_xp",
    "_format_voice",
    "_format_xp",
]
