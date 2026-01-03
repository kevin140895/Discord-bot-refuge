"""Commande /file pour ouvrir une file d'attente."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Dict, List

import discord
from discord import app_commands
from discord.ext import commands

from config import XP_VIEWER_ROLE_ID
from utils.interactions import safe_respond

logger = logging.getLogger(__name__)


@dataclass
class QueueState:
    creator_id: int
    name: str
    member_ids: List[int] = field(default_factory=list)
    is_closed: bool = False


class QueueView(discord.ui.View):
    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

        self.select_menu = QueueSelect(self.cog, self.channel_id)

        self.join_button = discord.ui.Button(
            label="REJOINDRE",
            style=discord.ButtonStyle.success,
        )
        self.join_button.callback = self._join

        self.close_button = discord.ui.Button(
            label="CLÔTURER",
            style=discord.ButtonStyle.danger,
        )
        self.close_button.callback = self._close

        self.add_item(self.select_menu)
        self.add_item(self.join_button)
        self.add_item(self.close_button)

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
            if isinstance(item, discord.ui.Select):
                item.disabled = True

    def update_options(self, queue: QueueState) -> None:
        self.select_menu.update_options(queue)

    async def _join(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_join(interaction, self)

    async def _close(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_close(interaction, self)


class QueueSelect(discord.ui.Select):
    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(
            placeholder="Choisir un joueur à valider",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Aucun joueur", value="none", description="File vide"
                )
            ],
            disabled=True,
        )
        self.cog = cog
        self.channel_id = channel_id

    def update_options(self, queue: QueueState) -> None:
        if queue.is_closed:
            self.disabled = True
            self.options = [
                discord.SelectOption(
                    label="File clôturée",
                    value="none",
                    description="Validation indisponible",
                )
            ]
            return

        if not queue.member_ids:
            self.disabled = True
            self.options = [
                discord.SelectOption(
                    label="Aucun joueur",
                    value="none",
                    description="File vide",
                )
            ]
            return

        self.disabled = False
        self.options = [
            discord.SelectOption(
                label=f"{index}. {member_id}",
                value=str(member_id),
                description=f"<@{member_id}>",
            )
            for index, member_id in enumerate(queue.member_ids, start=1)
        ]

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_validate_select(interaction, self)


class QueueCog(commands.Cog):
    """Gestion des files d'attente via /file."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queues: Dict[int, QueueState] = {}

    @app_commands.command(
        name="file",
        description="Ouvrir une file d'attente pour jouer avec le streamer",
    )
    @app_commands.checks.has_role(XP_VIEWER_ROLE_ID)
    @app_commands.describe(nom="Nom de la file d'attente")
    async def file(
        self,
        interaction: discord.Interaction,
        nom: str | None = None,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await safe_respond(
                interaction,
                "Commande utilisable uniquement sur un serveur.",
                ephemeral=True,
            )
            return

        channel_id = interaction.channel_id
        if channel_id is None:
            await safe_respond(
                interaction,
                "❌ Salon introuvable.",
                ephemeral=True,
            )
            return

        existing = self.queues.get(channel_id)
        if existing and not existing.is_closed:
            await safe_respond(
                interaction,
                "❌ Une file d'attente est déjà ouverte dans ce salon.",
                ephemeral=True,
            )
            return

        queue = QueueState(
            creator_id=interaction.user.id,
            name=nom.strip() if nom and nom.strip() else "File d'attente",
        )
        self.queues[channel_id] = queue

        embed = self._build_embed(queue)
        view = QueueView(self, channel_id)
        view.update_options(queue)
        await interaction.response.send_message(embed=embed, view=view)

    async def handle_join(
        self, interaction: discord.Interaction, view: QueueView
    ) -> None:
        queue = self.queues.get(view.channel_id)
        if queue is None:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est introuvable.",
                ephemeral=True,
            )
            return

        if queue.is_closed:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est clôturée.",
                ephemeral=True,
            )
            return

        if interaction.user.id in queue.member_ids:
            await safe_respond(
                interaction,
                "⚠️ Tu es déjà dans la file d'attente !",
                ephemeral=True,
            )
            return

        queue.member_ids.append(interaction.user.id)
        embed = self._build_embed(queue)
        view.update_options(queue)
        await self._edit_queue_message(interaction, embed, view)

    async def handle_close(
        self, interaction: discord.Interaction, view: QueueView
    ) -> None:
        queue = self.queues.get(view.channel_id)
        if queue is None:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est introuvable.",
                ephemeral=True,
            )
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(
                interaction,
                "❌ Seul le créateur peut clôturer la file !",
                ephemeral=True,
            )
            return

        if queue.is_closed:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est déjà clôturée.",
                ephemeral=True,
            )
            return

        queue.is_closed = True
        view.disable_all()
        embed = self._build_embed(queue)
        await self._edit_queue_message(interaction, embed, view)

    async def handle_validate_select(
        self, interaction: discord.Interaction, select: QueueSelect
    ) -> None:
        queue = self.queues.get(select.channel_id)
        if queue is None:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est introuvable.",
                ephemeral=True,
            )
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(
                interaction,
                "❌ Seul le créateur peut valider un joueur !",
                ephemeral=True,
            )
            return

        if queue.is_closed:
            await safe_respond(
                interaction,
                "❌ Cette file d'attente est clôturée.",
                ephemeral=True,
            )
            return

        selected = select.values[0]
        try:
            member_id = int(selected)
        except ValueError:
            await safe_respond(
                interaction,
                "⚠️ Aucun joueur à valider.",
                ephemeral=True,
            )
            return

        if member_id not in queue.member_ids:
            await safe_respond(
                interaction,
                "⚠️ Ce joueur n'est plus dans la file.",
                ephemeral=True,
            )
            return

        queue.member_ids.remove(member_id)
        embed = self._build_embed(queue)
        view = select.view
        if isinstance(view, QueueView):
            view.update_options(queue)
            await self._edit_queue_message(interaction, embed, view)

        # Notification du joueur validé
        if interaction.guild:
            member = interaction.guild.get_member(member_id)
        else:
            member = None
        
        if member:
            try:
                await member.send(
                    "Prépare toi tu as été choisis - Refuge"
                )
            except discord.HTTPException as exc:
                logger.warning(
                    "Impossible de notifier le joueur %s: %s",
                    member_id,
                    exc,
                )

    async def _edit_queue_message(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        view: QueueView,
    ) -> None:
        try:
            if interaction.response.is_done():
                if interaction.message:
                    await interaction.message.edit(embed=embed, view=view)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
        except discord.HTTPException as exc:
            logger.warning("Impossible de mettre à jour la file: %s", exc)

    def _build_embed(self, queue: QueueState) -> discord.Embed:
        if queue.is_closed:
            title = f"🔒 File d'attente clôturée — {queue.name}"
            color = discord.Color.red()
        else:
            title = f"✅ File d'attente ouverte — {queue.name}"
            color = discord.Color.green()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="👥 Participants",
            value=self._format_members(queue.member_ids),
            inline=False,
        )
        return embed

    def _format_members(self, member_ids: List[int]) -> str:
        if not member_ids:
            return "*(vide pour le moment)*"
        return "\n".join(
            f"{index}. <@{member_id}>" for index, member_id in enumerate(member_ids, start=1)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot))