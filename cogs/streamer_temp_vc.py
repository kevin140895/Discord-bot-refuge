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
    STREAMER_LOBBY_VC_ID,
    TEMP_VOICE_CATEGORY_ID,
    TRIGGER_CHANNEL_ID,
)
from storage.temp_vc_store import (
    load_streamer_temp_vcs,
    save_streamer_temp_vcs_async,
)
from view import StreamerTempVoiceView

logger = logging.getLogger(__name__)

STREAMER_TEMP_CHANNEL_PREFIX = "🔊・"


class StreamerTempVCCog(commands.Cog):
    """Création de salons vocaux temporaires réservés aux streamers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        persisted = load_streamer_temp_vcs()
        self._channel_to_owner: Dict[int, int] = dict(persisted)
        self._owner_to_channel: Dict[int, int] = {
            owner_id: channel_id for channel_id, owner_id in persisted.items()
        }
        self._delete_tasks: Dict[int, asyncio.Task] = {}
        self._owner_locks: Dict[int, asyncio.Lock] = {}

    def cog_unload(self) -> None:
        for task in self._delete_tasks.values():
            task.cancel()
        self._delete_tasks.clear()

    async def _persist_channels(self) -> None:
        await save_streamer_temp_vcs_async(self._channel_to_owner.copy())

    def _track_channel(self, owner_id: int, channel_id: int) -> None:
        previous_channel = self._owner_to_channel.get(owner_id)
        if previous_channel and previous_channel != channel_id:
            self._channel_to_owner.pop(previous_channel, None)
        self._owner_to_channel[owner_id] = channel_id
        self._channel_to_owner[channel_id] = owner_id

    def _forget_channel(self, channel_id: int) -> bool:
        owner_id = self._channel_to_owner.pop(channel_id, None)
        if owner_id is None:
            return False
        if self._owner_to_channel.get(owner_id) == channel_id:
            self._owner_to_channel.pop(owner_id, None)
        return True

    async def _untrack_channel(self, channel_id: int) -> None:
        if self._forget_channel(channel_id):
            await self._persist_channels()

    def _safe_name(self, member: discord.Member) -> str:
        base = (member.display_name or member.name).lower()
        safe = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
        return safe or "streamer"

    def _configured_category(
        self, guild: discord.Guild
    ) -> Optional[discord.CategoryChannel]:
        if TEMP_VOICE_CATEGORY_ID:
            category = guild.get_channel(TEMP_VOICE_CATEGORY_ID)
            if isinstance(category, discord.CategoryChannel):
                return category

        trigger = guild.get_channel(TRIGGER_CHANNEL_ID) if TRIGGER_CHANNEL_ID else None
        category = getattr(trigger, "category", None)
        if isinstance(category, discord.CategoryChannel):
            return category
        return None

    def _get_category(
        self, guild: discord.Guild, trigger_channel: discord.abc.GuildChannel
    ) -> Optional[discord.CategoryChannel]:
        category = self._configured_category(guild)
        if category is not None:
            return category
        fallback = getattr(trigger_channel, "category", None)
        return fallback if isinstance(fallback, discord.CategoryChannel) else None

    def _looks_like_streamer_temp(self, channel: object) -> bool:
        return str(getattr(channel, "name", "")).startswith(STREAMER_TEMP_CHANNEL_PREFIX)

    def _is_in_managed_category(
        self, guild: discord.Guild, channel: object
    ) -> bool:
        category = self._configured_category(guild)
        if category is None:
            return False
        if getattr(channel, "category", None) is category:
            return True
        return getattr(channel, "category_id", None) == getattr(category, "id", None)

    async def _get_existing_channel(
        self, guild: discord.Guild, owner_id: int
    ) -> Optional[discord.VoiceChannel]:
        channel_id = self._owner_to_channel.get(owner_id)
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            if self._forget_channel(channel_id):
                await self._persist_channels()
            return None
        return channel

    async def _adopt_existing_channel(
        self,
        member: discord.Member,
        trigger_channel: discord.abc.GuildChannel,
    ) -> Optional[discord.VoiceChannel]:
        """Récupère un ancien vocal non persisté si son propriétaire y est encore."""
        category = self._get_category(member.guild, trigger_channel)
        if category is None:
            return None

        expected_name = f"{STREAMER_TEMP_CHANNEL_PREFIX}{self._safe_name(member)}"
        matches = [
            channel
            for channel in category.voice_channels
            if channel.name == expected_name and member in channel.members
        ]
        if len(matches) != 1:
            return None

        channel = matches[0]
        self._track_channel(member.id, channel.id)
        await self._persist_channels()
        return channel

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
        }
        if bot_member is not None:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                move_members=True,
                connect=True,
            )

        category = self._get_category(guild, trigger_channel)
        name = f"{STREAMER_TEMP_CHANNEL_PREFIX}{self._safe_name(member)}"
        channel = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
        )
        self._track_channel(member.id, channel.id)
        try:
            await self._persist_channels()
        except Exception:
            self._forget_channel(channel.id)
            try:
                await channel.delete(reason="Échec de persistance du vocal temporaire")
            except Exception:
                logger.exception(
                    "[streamer_temp_vc] nettoyage après échec de persistance impossible"
                )
            raise
        return channel

    async def _get_or_create_channel(
        self,
        member: discord.Member,
        trigger_channel: discord.abc.GuildChannel,
    ) -> tuple[discord.VoiceChannel, bool]:
        """Retourne l'unique vocal du propriétaire, en le créant si nécessaire."""
        lock = self._owner_locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            existing = await self._get_existing_channel(member.guild, member.id)
            if existing is None:
                existing = await self._adopt_existing_channel(member, trigger_channel)
            if existing is not None:
                return existing, False
            return await self._create_channel(member, trigger_channel), True

    async def _delete_created_channel_after_move_failure(
        self, channel: discord.VoiceChannel
    ) -> None:
        """Supprime un salon créé dans ce flux si le propriétaire n'a pas pu y entrer."""
        try:
            await channel.delete(reason="Échec du déplacement du membre")
        except Exception:
            logger.exception(
                "[streamer_temp_vc] nettoyage après échec de déplacement impossible"
            )
            return
        try:
            await self._untrack_channel(channel.id)
        except Exception:
            logger.exception(
                "[streamer_temp_vc] persistance après nettoyage de déplacement échouée"
            )

    async def _delete_after_delay(self, channel_id: int) -> None:
        remove_mapping = False
        try:
            await asyncio.sleep(DELETE_DELAY_SECONDS)
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                remove_mapping = True
                return
            if channel.members:
                return
            await channel.delete(reason="Salon temporaire vide")
            remove_mapping = True
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[streamer_temp_vc] suppression du salon échouée")
        finally:
            self._delete_tasks.pop(channel_id, None)
            if remove_mapping:
                try:
                    await self._untrack_channel(channel_id)
                except Exception:
                    logger.exception(
                        "[streamer_temp_vc] persistance après suppression échouée"
                    )

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

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Réconcilie les vocaux persistés et nettoie les orphelins vides."""
        try:
            changed = False
            for channel_id in list(self._channel_to_owner):
                channel = self.bot.get_channel(channel_id)
                if not isinstance(channel, discord.VoiceChannel):
                    changed = self._forget_channel(channel_id) or changed
                    continue
                if not channel.members:
                    self._schedule_delete(channel_id)

            if changed:
                await self._persist_channels()

            for guild in getattr(self.bot, "guilds", []):
                category = self._configured_category(guild)
                if category is None:
                    continue
                for channel in list(category.voice_channels):
                    if channel.id in self._channel_to_owner:
                        continue
                    if not self._looks_like_streamer_temp(channel):
                        continue
                    if channel.members:
                        continue
                    try:
                        await channel.delete(reason="Salon streamer temporaire orphelin")
                    except discord.HTTPException:
                        logger.exception(
                            "[streamer_temp_vc] suppression d'un vocal orphelin échouée"
                        )
        except Exception:
            logger.exception("[streamer_temp_vc] réconciliation au démarrage échouée")

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

        try:
            channel, created = await self._get_or_create_channel(member, trigger_channel)
        except Exception:
            logger.exception("[streamer_temp_vc] création du salon échouée")
            await interaction.response.send_message(
                "Impossible de créer ton vocal pour le moment.",
                ephemeral=True,
            )
            return

        if not created:
            await interaction.response.send_message(
                f"Ton vocal existe déjà : {channel.mention}",
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
            await self._delete_created_channel_after_move_failure(channel)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if after.channel and after.channel.id in self._channel_to_owner:
            self._cancel_delete(after.channel.id)

        if before.channel and not before.channel.members:
            tracked = before.channel.id in self._channel_to_owner
            orphan = (
                self._looks_like_streamer_temp(before.channel)
                and self._is_in_managed_category(member.guild, before.channel)
            )
            if tracked or orphan:
                self._schedule_delete(before.channel.id)

        if not after.channel or after.channel.id != STREAMER_LOBBY_VC_ID:
            return

        role = member.guild.get_role(ALLOWED_ROLE_ID)
        if role is None or role not in member.roles:
            return

        try:
            channel, created = await self._get_or_create_channel(member, after.channel)
        except Exception:
            logger.exception(
                "[streamer_temp_vc] création depuis le lobby vocal échouée"
            )
            return

        try:
            await member.move_to(channel)
        except discord.HTTPException:
            logger.exception(
                "[streamer_temp_vc] déplacement depuis le lobby vocal échoué"
            )
            if created:
                await self._delete_created_channel_after_move_failure(channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        self._cancel_delete(channel.id)
        if channel.id not in self._channel_to_owner:
            return
        try:
            await self._untrack_channel(channel.id)
        except Exception:
            logger.exception(
                "[streamer_temp_vc] persistance après suppression externe échouée"
            )

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
