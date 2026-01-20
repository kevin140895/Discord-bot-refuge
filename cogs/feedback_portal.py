from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

import discord
from discord.ext import commands

from config import FEEDBACK_PORTAL_CHANNEL_ID, FEEDBACK_STAFF_CHANNEL_ID
from utils.interactions import safe_respond

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackConfig:
    label: str
    title: str
    color: int
    modal_title: str
    modal_custom_id: str


FEEDBACK_TYPES = {
    "suggestion": FeedbackConfig(
        label="Idée",
        title="💡 Idée",
        color=0x00FF00,
        modal_title="Nouvelle Idée",
        modal_custom_id="modal_suggestion",
    ),
    "bug": FeedbackConfig(
        label="Bug",
        title="🐛 Bug",
        color=0xFF0000,
        modal_title="Rapport de Bug",
        modal_custom_id="modal_bug",
    ),
    "avis": FeedbackConfig(
        label="Avis",
        title="⭐ Avis",
        color=0x0099FF,
        modal_title="Votre Avis",
        modal_custom_id="modal_avis",
    ),
}


def _extract_user_id(embed: discord.Embed) -> int | None:
    footer = embed.footer.text if embed.footer else ""
    match = re.search(r"(\d{5,})", footer)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _disable_buttons(view: discord.ui.View) -> None:
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True


class FeedbackPortalView(discord.ui.View):
    def __init__(self, cog: "FeedbackPortalCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Proposer une idée",
        style=discord.ButtonStyle.success,
        emoji="💡",
        custom_id="btn_suggestion",
    )
    async def suggestion(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(SuggestionModal(self.cog))

    @discord.ui.button(
        label="Signaler un bug",
        style=discord.ButtonStyle.danger,
        emoji="🐛",
        custom_id="btn_bug",
    )
    async def bug(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(BugReportModal(self.cog))

    @discord.ui.button(
        label="Donner un avis",
        style=discord.ButtonStyle.primary,
        emoji="⭐",
        custom_id="btn_avis",
    )
    async def avis(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(OpinionModal(self.cog))


class FeedbackStaffView(discord.ui.View):
    def __init__(self, cog: "FeedbackPortalCog", *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if disabled:
            _disable_buttons(self)

    @discord.ui.button(
        label="Valider",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="staff_approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_staff_action(interaction, "approve")

    @discord.ui.button(
        label="Refuser",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="staff_reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_staff_action(interaction, "reject")

    @discord.ui.button(
        label="Supprimer",
        style=discord.ButtonStyle.secondary,
        emoji="🗑️",
        custom_id="staff_delete",
    )
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_staff_action(interaction, "delete")


class SuggestionModal(discord.ui.Modal):
    def __init__(self, cog: "FeedbackPortalCog") -> None:
        cfg = FEEDBACK_TYPES["suggestion"]
        super().__init__(title=cfg.modal_title, custom_id=cfg.modal_custom_id)
        self.cog = cog
        self.idea_title = discord.ui.TextInput(
            label="Titre de l'idée",
            placeholder='ex: "Salon Musique"',
        )
        self.idea_description = discord.ui.TextInput(
            label="Description détaillée",
            placeholder='ex: "Pourquoi c\'est utile..."',
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.idea_title)
        self.add_item(self.idea_description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_submission(
            interaction,
            "suggestion",
            (
                ("Titre de l'idée", self.idea_title.value),
                ("Description détaillée", self.idea_description.value),
            ),
        )


class BugReportModal(discord.ui.Modal):
    def __init__(self, cog: "FeedbackPortalCog") -> None:
        cfg = FEEDBACK_TYPES["bug"]
        super().__init__(title=cfg.modal_title, custom_id=cfg.modal_custom_id)
        self.cog = cog
        self.system = discord.ui.TextInput(
            label="Système impacté",
            placeholder='ex: "Commande /rank"',
        )
        self.problem = discord.ui.TextInput(
            label="Description du problème",
            placeholder='ex: "Le bot ne répond pas..."',
            style=discord.TextStyle.paragraph,
        )
        self.repro = discord.ui.TextInput(
            label="Reproduction (Optionnel)",
            placeholder='ex: "Cliquez ici puis là..."',
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.add_item(self.system)
        self.add_item(self.problem)
        self.add_item(self.repro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_submission(
            interaction,
            "bug",
            (
                ("Système impacté", self.system.value),
                ("Description du problème", self.problem.value),
                ("Reproduction", self.repro.value),
            ),
        )


class OpinionModal(discord.ui.Modal):
    def __init__(self, cog: "FeedbackPortalCog") -> None:
        cfg = FEEDBACK_TYPES["avis"]
        super().__init__(title=cfg.modal_title, custom_id=cfg.modal_custom_id)
        self.cog = cog
        self.rating = discord.ui.TextInput(
            label="Note /5",
            placeholder='ex: "5/5"',
        )
        self.comment = discord.ui.TextInput(
            label="Commentaire",
            placeholder='ex: "Super serveur mais..."',
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.rating)
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_submission(
            interaction,
            "avis",
            (
                ("Note /5", self.rating.value),
                ("Commentaire", self.comment.value),
            ),
        )


class FeedbackPortalCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._portal_checked = False

    def cog_load(self) -> None:
        if not getattr(self.bot, "_feedback_views_added", False):
            self.bot.add_view(FeedbackPortalView(self))
            self.bot.add_view(FeedbackStaffView(self))
            self.bot._feedback_views_added = True

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._portal_checked:
            return
        self._portal_checked = True
        await self.ensure_portal_message()

    async def ensure_portal_message(self) -> None:
        channel = self.bot.get_channel(FEEDBACK_PORTAL_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(FEEDBACK_PORTAL_CHANNEL_ID)
            except discord.HTTPException:
                logger.warning("[feedback] portal channel introuvable")
                return
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("[feedback] portal channel non compatible")
            return

        target_title = "📬 Centre de Retours & Support"
        async for message in channel.history(limit=50):
            if message.author.id != self.bot.user.id:
                continue
            if message.embeds and message.embeds[0].title == target_title:
                return

        embed = discord.Embed(
            title=target_title,
            description=(
                "Bienvenue avec l'equipe technique du refuge."
            ),
            color=discord.Color.gold(),
        )
        await channel.send(embed=embed, view=FeedbackPortalView(self))

    async def handle_submission(
        self,
        interaction: discord.Interaction,
        feedback_type: str,
        fields: Iterable[tuple[str, str]],
    ) -> None:
        cfg = FEEDBACK_TYPES[feedback_type]
        staff_channel = self.bot.get_channel(FEEDBACK_STAFF_CHANNEL_ID)
        if staff_channel is None:
            try:
                staff_channel = await self.bot.fetch_channel(
                    FEEDBACK_STAFF_CHANNEL_ID
                )
            except discord.HTTPException:
                staff_channel = None
        if not isinstance(staff_channel, discord.abc.Messageable):
            await safe_respond(
                interaction,
                "❌ Salon staff introuvable.",
                ephemeral=True,
            )
            logger.warning("[feedback] staff channel introuvable")
            return

        embed = discord.Embed(
            title=cfg.title,
            color=discord.Color(cfg.color),
            timestamp=discord.utils.utcnow(),
        )
        for label, value in fields:
            embed.add_field(
                name=label,
                value=value or "Non renseigné",
                inline=False,
            )
        avatar_url = getattr(getattr(interaction.user, "display_avatar", None), "url", None)
        embed.set_author(name=str(interaction.user), icon_url=avatar_url)
        embed.set_footer(text=f"ID: {interaction.user.id}")

        await staff_channel.send(embed=embed, view=FeedbackStaffView(self))
        await safe_respond(
            interaction,
            "✅ Merci ! Ton retour a bien été transmis à l'équipe de modération.",
            ephemeral=True,
        )

    async def handle_staff_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        if interaction.channel_id != FEEDBACK_STAFF_CHANNEL_ID:
            await safe_respond(
                interaction,
                "Action réservée au salon staff.",
                ephemeral=True,
            )
            return
        if interaction.message is None:
            await safe_respond(interaction, "Message introuvable.", ephemeral=True)
            return

        if action == "delete":
            await interaction.message.delete()
            return

        if not interaction.message.embeds:
            await safe_respond(interaction, "Embed introuvable.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        title = embed.title or "Retour"
        if action == "approve":
            color = discord.Color(0x006400)
            status = "[VALIDÉ]"
            dm_message = "Ton idée/rapport a été validé par l'équipe !"
        else:
            color = discord.Color(0x808080)
            status = "[REFUSÉ]"
            dm_message = "Merci de ton retour, mais nous ne donnerons pas suite pour l'instant."

        if status not in title:
            title = f"{status} {title}"
        embed = embed.copy()
        embed.title = title
        embed.color = color

        await interaction.response.edit_message(
            embed=embed,
            view=FeedbackStaffView(self, disabled=True),
        )

        user_id = _extract_user_id(embed)
        if user_id is None:
            return
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        if user is None:
            return
        try:
            await user.send(dm_message)
        except discord.HTTPException:
            logger.info("[feedback] DM refusé pour %s", user_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedbackPortalCog(bot))
