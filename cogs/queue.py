"""Commande /file pour ouvrir une file d'attente."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional

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
    guild_id: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    member_ids: List[int] = field(default_factory=list)
    selected_ids: List[int] = field(default_factory=list)
    is_closed: bool = False


class CloseConfirmView(discord.ui.View):
    """Vue de confirmation (éphémère) avant clôture."""

    def __init__(self, cog: "QueueCog", channel_id: int, queue_message_id: int) -> None:
        super().__init__(timeout=30)
        self.cog = cog
        self.channel_id = channel_id
        self.queue_message_id = queue_message_id

    @discord.ui.button(label="Confirmer la clôture", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_close_confirm(
            interaction, channel_id=self.channel_id, queue_message_id=self.queue_message_id
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await safe_respond(interaction, "Clôture annulée.", ephemeral=True)
        self.stop()


class RemoveSelectedView(discord.ui.View):
    """Vue pour retirer quelqu'un des sélectionnés."""

    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id
        
        queue = cog.queues.get(channel_id)
        if queue and queue.selected_ids:
            self.select_menu = RemoveSelectedSelect(cog, channel_id)
            self.add_item(self.select_menu)
        else:
            # Pas de sélectionnés, on ajoute juste un label
            self.add_item(discord.ui.Button(
                label="Aucun sélectionné",
                style=discord.ButtonStyle.secondary,
                disabled=True
            ))


class RemoveSelectedSelect(discord.ui.Select):
    """Select menu pour choisir qui retirer des sélectionnés."""

    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(
            placeholder="Choisir un sélectionné à retirer",
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.channel_id = channel_id
        self.update_options()

    def update_options(self) -> None:
        queue = self.cog.queues.get(self.channel_id)
        if not queue or not queue.selected_ids:
            self.options = [
                discord.SelectOption(label="Aucun sélectionné", value="none")
            ]
            self.disabled = True
            return

        guild = self.cog.bot.get_guild(queue.guild_id)
        options: List[discord.SelectOption] = []
        
        for index, member_id in enumerate(queue.selected_ids, start=1):
            display = str(member_id)
            if guild:
                m = guild.get_member(member_id)
                if m:
                    display = m.display_name

            options.append(
                discord.SelectOption(
                    label=f"{index}. {display}",
                    value=str(member_id),
                    description=f"<@{member_id}>",
                )
            )

        self.options = options
        self.disabled = False

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_remove_selected(interaction, self.channel_id)


class QueueView(discord.ui.View):
    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

        self.select_menu = QueueSelect(self.cog, self.channel_id)

        self.join_button = discord.ui.Button(
            label="➕ Rejoindre",
            style=discord.ButtonStyle.success,
        )
        self.join_button.callback = self._join

        self.leave_button = discord.ui.Button(
            label="➖ Quitter",
            style=discord.ButtonStyle.secondary,
        )
        self.leave_button.callback = self._leave

        self.remove_selected_button = discord.ui.Button(
            label="❌ Retirer des sélectionnés",
            style=discord.ButtonStyle.secondary,
        )
        self.remove_selected_button.callback = self._remove_selected

        self.close_button = discord.ui.Button(
            label="🔒 Clôturer",
            style=discord.ButtonStyle.danger,
        )
        self.close_button.callback = self._close

        self.add_item(self.select_menu)
        self.add_item(self.join_button)
        self.add_item(self.leave_button)
        self.add_item(self.remove_selected_button)
        self.add_item(self.close_button)

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    def update_options(self, queue: QueueState) -> None:
        self.select_menu.update_options(queue)

        # Si clôturée : on désactive aussi les boutons d'action.
        if queue.is_closed:
            self.join_button.disabled = True
            self.leave_button.disabled = True
            self.remove_selected_button.disabled = True
            self.close_button.disabled = True

    async def _join(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_join(interaction, self)

    async def _leave(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_leave(interaction, self)

    async def _remove_selected(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_remove_selected_request(interaction, self.channel_id)

    async def _close(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_close_request(interaction, self)


class QueueSelect(discord.ui.Select):
    def __init__(self, cog: "QueueCog", channel_id: int) -> None:
        super().__init__(
            placeholder="Choisir un joueur à valider",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Aucun joueur", value="none", description="File vide")
            ],
            disabled=True,
        )
        self.cog = cog
        self.channel_id = channel_id

    def update_options(self, queue: QueueState) -> None:
        if queue.is_closed:
            self.disabled = True
            self.placeholder = "File clôturée"
            self.options = [
                discord.SelectOption(
                    label="Validation indisponible",
                    value="none",
                    description="La file est clôturée",
                )
            ]
            return

        if not queue.member_ids:
            self.disabled = True
            self.placeholder = "File vide"
            self.options = [
                discord.SelectOption(
                    label="Aucun joueur",
                    value="none",
                    description="Personne n'attend pour le moment",
                )
            ]
            return

        self.disabled = False
        self.placeholder = "Sélectionner un joueur à valider"

        # On tente d'afficher un nom lisible, sinon fallback sur l'ID.
        guild = self.cog.bot.get_guild(queue.guild_id)
        options: List[discord.SelectOption] = []
        for index, member_id in enumerate(queue.member_ids, start=1):
            display = str(member_id)
            if guild:
                m = guild.get_member(member_id)
                if m:
                    display = m.display_name

            options.append(
                discord.SelectOption(
                    label=f"{index}. {display}",
                    value=str(member_id),
                    description=f"<@{member_id}>",
                )
            )

        self.options = options

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
            await safe_respond(interaction, "Salon introuvable.", ephemeral=True)
            return

        existing = self.queues.get(channel_id)
        if existing and not existing.is_closed:
            await safe_respond(
                interaction,
                "Une file d'attente est déjà ouverte dans ce salon.",
                ephemeral=True,
            )
            return

        queue = QueueState(
            creator_id=interaction.user.id,
            name=nom.strip() if nom and nom.strip() else "File d'attente",
            guild_id=interaction.guild.id,
        )
        self.queues[channel_id] = queue

        embed = self._build_embed(queue)
        view = QueueView(self, channel_id)
        view.update_options(queue)
        await interaction.response.send_message(embed=embed, view=view)

    async def handle_join(self, interaction: discord.Interaction, view: QueueView) -> None:
        queue = self.queues.get(view.channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est clôturée.", ephemeral=True)
            return

        if interaction.user.id in queue.member_ids:
            pos = queue.member_ids.index(interaction.user.id) + 1
            await safe_respond(interaction, f"Tu es déjà dans la file (position {pos}).", ephemeral=True)
            return

        if interaction.user.id in queue.selected_ids:
            await safe_respond(interaction, "Tu as déjà été sélectionné.", ephemeral=True)
            return

        queue.member_ids.append(interaction.user.id)
        pos = len(queue.member_ids)

        embed = self._build_embed(queue)
        view.update_options(queue)
        await self._edit_queue_message(interaction, embed, view)

        await safe_respond(interaction, f"✅ Ajouté à la file (position {pos}).", ephemeral=True)

    async def handle_leave(self, interaction: discord.Interaction, view: QueueView) -> None:
        queue = self.queues.get(view.channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est clôturée.", ephemeral=True)
            return

        if interaction.user.id not in queue.member_ids:
            await safe_respond(interaction, "Tu n'es pas dans la file.", ephemeral=True)
            return

        queue.member_ids.remove(interaction.user.id)
        embed = self._build_embed(queue)
        view.update_options(queue)
        await self._edit_queue_message(interaction, embed, view)

        await safe_respond(interaction, "➖ Retiré de la file.", ephemeral=True)

    async def handle_remove_selected_request(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        queue = self.queues.get(channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(interaction, "Seul le créateur peut retirer des sélectionnés.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est clôturée.", ephemeral=True)
            return

        if not queue.selected_ids:
            await safe_respond(interaction, "Aucun joueur sélectionné pour le moment.", ephemeral=True)
            return

        remove_view = RemoveSelectedView(self, channel_id)
        await safe_respond(
            interaction,
            "Choisir un sélectionné à retirer :",
            ephemeral=True,
            view=remove_view,
        )

    async def handle_remove_selected(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        queue = self.queues.get(channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(interaction, "Seul le créateur peut retirer des sélectionnés.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est clôturée.", ephemeral=True)
            return

        # Récupérer le select menu depuis la view
        if not interaction.data or "values" not in interaction.data:
            await safe_respond(interaction, "Erreur lors de la récupération du choix.", ephemeral=True)
            return

        selected = interaction.data["values"][0]
        try:
            member_id = int(selected)
        except ValueError:
            await safe_respond(interaction, "Choix invalide.", ephemeral=True)
            return

        if member_id not in queue.selected_ids:
            await safe_respond(interaction, "Ce joueur n'est plus sélectionné.", ephemeral=True)
            return

        # Le retirer des sélectionnés
        queue.selected_ids.remove(member_id)

        # Récupérer la vue et mettre à jour l'embed
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            try:
                # On cherche le message principal de la file
                async for msg in channel.history(limit=50):
                    if msg.author == self.bot.user and msg.embeds:
                        embed = msg.embeds[0]
                        if f"File d'attente" in embed.title:
                            view = QueueView(self, channel_id)
                            view.update_options(queue)
                            new_embed = self._build_embed(queue)
                            await msg.edit(embed=new_embed, view=view)
                            break
            except discord.HTTPException:
                pass

        await safe_respond(interaction, f"✅ <@{member_id}> a été retiré des sélectionnés.", ephemeral=True)

        # Envoyer un DM au joueur
        guild = self.bot.get_guild(queue.guild_id)
        if guild:
            member = guild.get_member(member_id)
            if member:
                try:
                    await member.send(f"Tu as été retiré des sélectionnés de la file '{queue.name}'. Tu peux rejoindre à nouveau.")
                except discord.HTTPException as exc:
                    logger.warning("Impossible de notifier le joueur %s: %s", member_id, exc)

    async def handle_close_request(self, interaction: discord.Interaction, view: QueueView) -> None:
        queue = self.queues.get(view.channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(interaction, "Seul le créateur peut clôturer la file.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est déjà clôturée.", ephemeral=True)
            return

        if interaction.message is None:
            await safe_respond(interaction, "Message de file introuvable.", ephemeral=True)
            return

        confirm_view = CloseConfirmView(self, view.channel_id, interaction.message.id)
        await safe_respond(
            interaction,
            "Confirmer la clôture de la file ?",
            ephemeral=True,
            view=confirm_view,
        )

    async def handle_close_confirm(
        self, interaction: discord.Interaction, channel_id: int, queue_message_id: int
    ) -> None:
        queue = self.queues.get(channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(interaction, "Seul le créateur peut clôturer la file.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est déjà clôturée.", ephemeral=True)
            return

        queue.is_closed = True

        # Edit du message principal (celui qui contient la view)
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await safe_respond(interaction, "Salon introuvable.", ephemeral=True)
            return

        try:
            msg = await channel.fetch_message(queue_message_id)
        except discord.HTTPException:
            await safe_respond(interaction, "Impossible de retrouver le message de la file.", ephemeral=True)
            return

        view = QueueView(self, channel_id)
        view.disable_all()
        view.update_options(queue)
        embed = self._build_embed(queue)

        try:
            await msg.edit(embed=embed, view=view)
        except discord.HTTPException as exc:
            logger.warning("Impossible de clôturer la file (edit): %s", exc)

        await safe_respond(interaction, "🔒 File clôturée.", ephemeral=True)

    async def handle_validate_select(
        self, interaction: discord.Interaction, select: QueueSelect
    ) -> None:
        queue = self.queues.get(select.channel_id)
        if queue is None:
            await safe_respond(interaction, "Cette file d'attente est introuvable.", ephemeral=True)
            return

        if interaction.user.id != queue.creator_id:
            await safe_respond(interaction, "Seul le créateur peut valider un joueur.", ephemeral=True)
            return

        if queue.is_closed:
            await safe_respond(interaction, "Cette file d'attente est clôturée.", ephemeral=True)
            return

        selected = select.values[0]
        try:
            member_id = int(selected)
        except ValueError:
            await safe_respond(interaction, "Aucun joueur à valider.", ephemeral=True)
            return

        if member_id not in queue.member_ids:
            await safe_respond(interaction, "Ce joueur n'est plus dans la file.", ephemeral=True)
            return

        # Déplacer dans selected_ids
        queue.member_ids.remove(member_id)
        queue.selected_ids.append(member_id)

        embed = self._build_embed(queue)

        view = select.view
        if isinstance(view, QueueView):
            view.update_options(queue)
            await self._edit_queue_message(interaction, embed, view)

        await safe_respond(interaction, f"✅ Joueur sélectionné : <@{member_id}>", ephemeral=True)

        # Notification du joueur validé
        member: Optional[discord.Member] = None
        if interaction.guild:
            member = interaction.guild.get_member(member_id)

        if member:
            try:
                await member.send("Prépare-toi, tu as été sélectionné — Refuge")
            except discord.HTTPException as exc:
                logger.warning("Impossible de notifier le joueur %s: %s", member_id, exc)

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
        status = "clôturée" if queue.is_closed else "ouverte"
        title = f"File d'attente {status} — {queue.name}"
        color = discord.Color.red() if queue.is_closed else discord.Color.green()

        embed = discord.Embed(title=title, color=color, timestamp=queue.created_at)

        embed.add_field(name="👤 Créateur", value=f"<@{queue.creator_id}>", inline=True)
        embed.add_field(name="⏳ En attente", value=str(len(queue.member_ids)), inline=True)
        embed.add_field(name="✅ Sélectionnés", value=str(len(queue.selected_ids)), inline=True)

        # Liste des joueurs en attente
        embed.add_field(
            name="⏳ File d'attente",
            value=self._format_members(queue.member_ids),
            inline=False,
        )

        # Liste des sélectionnés
        embed.add_field(
            name="✅ Sélectionnés",
            value=self._format_members(queue.selected_ids),
            inline=False,
        )

        if not queue.is_closed:
            embed.set_footer(text="Utilise ➕ Rejoindre / ➖ Quitter. Le créateur peut valider via le menu ou retirer des sélectionnés.")
        else:
            embed.set_footer(text="🔒 File clôturée.")

        return embed

    def _format_members(self, member_ids: List[int]) -> str:
        if not member_ids:
            return "*(vide)*"

        # Evite de dépasser les limites de champs embed (1024 chars).
        lines = []
        for index, member_id in enumerate(member_ids, start=1):
            lines.append(f"{index}. <@{member_id}>")

        text = "\n".join(lines)
        if len(text) <= 1024:
            return text

        # Troncature "safe"
        safe_lines: List[str] = []
        total = 0
        for line in lines:
            if total + len(line) + 1 > 1000:
                break
            safe_lines.append(line)
            total += len(line) + 1

        remaining = len(lines) - len(safe_lines)
        return "\n".join(safe_lines) + f"\n… (+{remaining} autres)"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot))
