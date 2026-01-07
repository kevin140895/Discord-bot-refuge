import asyncio
import logging
import re
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    ALLOWED_ROLE_ID,
    DELETE_DELAY_SECONDS,
    TEMP_VOICE_CATEGORY_ID,
    TRIGGER_CHANNEL_ID,
)
from view import StreamerTempVoiceView

logger = logging.getLogger(__name__)


class StreamerTempVCCog(commands.Cog):
    """Création de salons vocaux temporaires réservés aux streamers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._owner_to_channel: Dict[int, int] = {}
        self._channel_to_owner: Dict[int, int] = {}
        self._delete_tasks: Dict[int, asyncio.Task] = {}

    def _safe_name(self, member: discord.Member) -> str:
        base = (member.display_name or member.name).lower()
        safe = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
        return safe or "streamer"

    def _get_existing_channel(
        self, guild: discord.Guild, owner_id: int
    ) -> Optional[discord.VoiceChannel]:
        channel_id = self._owner_to_channel.get(owner_id)
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            self._owner_to_channel.pop(owner_id, None)
            self._channel_to_owner.pop(channel_id, None)
            return None
        return channel

    def _get_category(
        self, guild: discord.Guild, trigger_channel: discord.abc.GuildChannel
    ) -> Optional[discord.CategoryChannel]:
        if TEMP_VOICE_CATEGORY_ID:
            category = guild.get_channel(TEMP_VOICE_CATEGORY_ID)
            if isinstance(category, discord.CategoryChannel):
                return category
        return trigger_channel.category

    async def _create_channel(
        self, member: discord.Member, trigger_channel: discord.abc.GuildChannel
    ) -> discord.VoiceChannel:
        guild = member.guild
        role = guild.get_role(ALLOWED_ROLE_ID)
        if role is None:
            raise RuntimeError("ALLOWED_ROLE_ID invalide")

        bot_member = None
        if self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
                connect=False,
            ),
            role: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
            ),
        }
        if bot_member is not None:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                move_members=True,
                connect=True,
            )

        category = self._get_category(guild, trigger_channel)
        name = f"🔊・{self._safe_name(member)}"
        channel = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
        )
        self._owner_to_channel[member.id] = channel.id
        self._channel_to_owner[channel.id] = member.id
        return channel

    async def _delete_after_delay(self, channel_id: int) -> None:
        try:
            await asyncio.sleep(DELETE_DELAY_SECONDS)
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return
            if channel.members:
                return
            await channel.delete(reason="Salon temporaire vide")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[streamer_temp_vc] suppression du salon échouée")
        finally:
            owner_id = self._channel_to_owner.pop(channel_id, None)
            if owner_id:
                self._owner_to_channel.pop(owner_id, None)
            self._delete_tasks.pop(channel_id, None)

    def _schedule_delete(self, channel_id: int) -> None:
        task = self._delete_tasks.pop(channel_id, None)
        if task:
            task.cancel()
        self._delete_tasks[channel_id] = asyncio.create_task(
            self._delete_after_delay(channel_id)
        )

    def _cancel_delete(self, channel_id: int) -> None:
        task = self._delete_tasks.pop(channel_id, None)
        if task:
            task.cancel()

    async def handle_create_request(
        self, interaction: discord.Interaction
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Action impossible en message privé.",
                ephemeral=True,
            )
            return

        trigger_channel = guild.get_channel(TRIGGER_CHANNEL_ID)
        if trigger_channel and isinstance(trigger_channel, discord.abc.Messageable):
            if interaction.channel_id != TRIGGER_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Utilise ce bouton dans <#{TRIGGER_CHANNEL_ID}>.",
                    ephemeral=True,
                )
                return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ Impossible de récupérer ton profil.",
                ephemeral=True,
            )
            return

        role = guild.get_role(ALLOWED_ROLE_ID)
        if role is None or role not in member.roles:
            await interaction.response.send_message(
                "Accès refusé.",
                ephemeral=True,
            )
            return

        if trigger_channel is None and isinstance(interaction.channel, discord.abc.GuildChannel):
            trigger_channel = interaction.channel
        if trigger_channel is None:
            await interaction.response.send_message(
                "Salon déclencheur introuvable.",
                ephemeral=True,
            )
            return

        existing = self._get_existing_channel(guild, member.id)
        if existing is not None:
            await interaction.response.send_message(
                f"Ton vocal existe déjà : {existing.mention}",
                ephemeral=True,
            )
            return

        try:
            channel = await self._create_channel(member, trigger_channel)
        except Exception:
            logger.exception("[streamer_temp_vc] création du salon échouée")
            await interaction.response.send_message(
                "Impossible de créer ton vocal pour le moment.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Salon créé : {channel.mention}. Tu peux le rejoindre.",
            ephemeral=True,
        )

        try:
            await member.move_to(channel)
        except discord.HTTPException:
            logger.exception(
                "[streamer_temp_vc] déplacement dans le salon échoué"
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if after.channel and after.channel.id in self._channel_to_owner:
            self._cancel_delete(after.channel.id)

        if before.channel and before.channel.id in self._channel_to_owner:
            if not before.channel.members:
                self._schedule_delete(before.channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        if channel.id not in self._channel_to_owner:
            return
        owner_id = self._channel_to_owner.pop(channel.id, None)
        if owner_id:
            self._owner_to_channel.pop(owner_id, None)
        self._cancel_delete(channel.id)

    @app_commands.command(
        name="streamer_vocal_message",
        description="Publier le bouton de création de vocal streamer.",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def post_button_message(
        self, interaction: discord.Interaction
    ) -> None:
        guild = interaction.guild
        trigger_channel = guild.get_channel(TRIGGER_CHANNEL_ID) if guild else None
        if trigger_channel and isinstance(trigger_channel, discord.abc.Messageable):
            if interaction.channel_id != TRIGGER_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Utilise cette commande dans <#{TRIGGER_CHANNEL_ID}>.",
                    ephemeral=True,
                )
                return

        await interaction.channel.send(
            "Clique sur le bouton pour créer ton vocal streamer.",
            view=StreamerTempVoiceView(self.bot),
        )
        await interaction.response.send_message(
            "Message envoyé.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StreamerTempVCCog(bot))
