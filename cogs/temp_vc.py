import asyncio
import logging
from typing import Dict, Set

import discord
from discord.ext import commands, tasks

from config import (
    LOBBY_VC_ID,
    ROLE_CONSOLE,
    ROLE_MOBILE,
    ROLE_PC,
    TEMP_VC_CATEGORY,
    TEMP_VC_LIMITS,
    RENAME_DELAY,
    TEMP_VC_CHECK_INTERVAL_SECONDS,
)
from storage.temp_vc_store import load_temp_vc_ids, save_temp_vc_ids
from utils.temp_vc_cleanup import delete_untracked_temp_vcs, TEMP_VC_NAME_RE
from utils.rename_manager import rename_manager
logger = logging.getLogger(__name__)

# IDs des salons vocaux temporaires connus
TEMP_VC_IDS: Set[int] = set(load_temp_vc_ids())

# Mapping « rôle principal → nom de base du salon »
ROLE_NAMES: Dict[int, str] = {
    ROLE_PC: "PC",
    ROLE_CONSOLE: "Console",
    ROLE_MOBILE: "Mobile",
}


class TempVCCog(commands.Cog):
    """Création et maintenance des salons vocaux temporaires."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._rename_tasks: Dict[int, asyncio.Task] = {}
        self._last_names: Dict[int, str] = {}

        if not TEMP_VC_IDS:
            getter = getattr(bot, "get_channel", lambda _id: None)
            category = getter(TEMP_VC_CATEGORY)
            if isinstance(category, discord.CategoryChannel):
                for ch in category.voice_channels:
                    base = ch.name.split("•", 1)[0].strip()
                    if TEMP_VC_NAME_RE.match(base):
                        TEMP_VC_IDS.add(ch.id)
                        self._last_names[ch.id] = ch.name
                if TEMP_VC_IDS:
                    save_temp_vc_ids(TEMP_VC_IDS)

        self.cleanup.start()

        if rename_manager._worker is None or rename_manager._worker.done():
            loop = getattr(self.bot, "loop", None)
            if loop is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
            if loop:
                loop.create_task(self._ensure_rename_worker())

    def cog_unload(self) -> None:
        self.cleanup.cancel()
        for task in self._rename_tasks.values():
            task.cancel()
        self._rename_tasks.clear()

    async def _ensure_rename_worker(self) -> bool:
        """Start the rename manager worker if it's not running.

        Returns True if the worker is active, False otherwise."""
        if rename_manager._worker is None or rename_manager._worker.done():
            try:
                await rename_manager.start()
            except Exception:
                logger.exception(
                    "[temp_vc] échec du démarrage du worker rename_manager"
                )
                return False
            else:
                logger.info("[temp_vc] rename_manager worker démarré")
        return True

    # ---------- outils internes ----------

    def _base_name_for(self, member: discord.Member) -> str:
        """Retourne le nom de base du salon selon le rôle principal."""
        for rid, name in ROLE_NAMES.items():
            if member.get_role(rid):
                return name
        return "Chat"

    def _base_name_from_members(self, members: list[discord.Member]) -> str:
        """Détermine le nom principal selon les rôles des membres du salon."""
        platforms = {
            self._base_name_for(m)
            for m in members
            if self._base_name_for(m) != "Chat"
        }
        if len(platforms) == 1:
            return next(iter(platforms))
        if len(platforms) > 1:
            return "Crossplay"
        return "Chat"

    def _compute_channel_name(self, channel: discord.VoiceChannel) -> str | None:
        """Calcule le nom attendu pour le salon selon les membres."""
        if not channel.members:
            return None
        base = self._base_name_from_members(channel.members)

        # Priorité : nom du jeu > "Endormie" > "Chat"
        game_name = None
        for m in channel.members:
            for act in m.activities:
                if isinstance(act, discord.Game) or (
                    isinstance(act, discord.Activity)
                    and act.type is discord.ActivityType.playing
                ):
                    game_name = act.name
                    break
            if game_name:
                break

        if game_name:
            # Discord limite les noms de salon à 100 caractères ; on réserve
            # l'espace pour la base et le séparateur «  • ».
            max_status_len = 100 - len(base) - 3
            status = game_name[:max_status_len]
        elif any(m.voice and m.voice.self_mute for m in channel.members):
            status = "Endormie"
        else:
            status = "Chat"
        name = f"{base} • {status}"
        # Discord limite les noms de salon à 100 caractères
        return name[:100]

    async def _rename_channel(self, channel: discord.VoiceChannel) -> None:
        """Tâche différée effectuant le renommage du salon."""
        try:
            await asyncio.sleep(RENAME_DELAY)
            task = asyncio.current_task()
            if self._rename_tasks.get(channel.id) is not task:
                return
            # Le salon peut avoir été supprimé pendant l'attente
            if getattr(channel, "guild", None) and channel.guild.get_channel(channel.id) is None:
                return

            new = self._compute_channel_name(channel)
            if new and channel.name != new:
                if await self._ensure_rename_worker():
                    await rename_manager.request(channel, new)
                    self._last_names[channel.id] = new
        except asyncio.CancelledError:
            pass
        finally:
            if self._rename_tasks.get(channel.id) is asyncio.current_task():
                self._rename_tasks.pop(channel.id, None)

    async def _update_channel_name(self, channel: discord.VoiceChannel) -> None:
        """Programme ou reprogramme le renommage du salon après un délai."""
        new = self._compute_channel_name(channel)
        cached = self._last_names.get(channel.id)
        if new is None:
            return
        if cached == new and channel.name == new:
            return
        self._last_names[channel.id] = new
        task = self._rename_tasks.get(channel.id)
        if task:
            task.cancel()
        if not await self._ensure_rename_worker():
            return
        new_task = self.bot.loop.create_task(self._rename_channel(channel))
        self._rename_tasks[channel.id] = new_task

    async def _create_temp_vc(self, member: discord.Member) -> discord.VoiceChannel:
        """Crée un salon vocal temporaire et l’enregistre."""
        category = self.bot.get_channel(TEMP_VC_CATEGORY)
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("TEMP_VC_CATEGORY invalide")

        base = self._base_name_for(member)
        limit = TEMP_VC_LIMITS.get(TEMP_VC_CATEGORY)
        channel = await category.create_voice_channel(base, user_limit=limit)

        TEMP_VC_IDS.add(channel.id)
        self._last_names[channel.id] = channel.name
        save_temp_vc_ids(TEMP_VC_IDS)
        return channel

    # ----------- événements Discord -----------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # 1) Création quand on rejoint le lobby
        if after.channel and after.channel.id == LOBBY_VC_ID:
            new_vc = await self._create_temp_vc(member)
            logger.info(
                "[temp_vc] created temporary channel '%s' (ID %s) for %s (%s)",
                new_vc.name,
                new_vc.id,
                member,
                member.id,
            )
            try:
                await member.move_to(new_vc)
                logger.debug(
                    "[temp_vc] moved %s (%s) into temporary channel '%s' (ID %s)",
                    member,
                    member.id,
                    new_vc.name,
                    new_vc.id,
                )
            except discord.HTTPException:
                logger.exception(
                    "[temp_vc] failed to move %s (%s) into temporary channel '%s' (ID %s)",
                    member,
                    member.id,
                    new_vc.name,
                    new_vc.id,
                )
                await new_vc.delete(reason="Échec du déplacement du membre")
                TEMP_VC_IDS.discard(new_vc.id)
                self._last_names.pop(new_vc.id, None)
                save_temp_vc_ids(TEMP_VC_IDS)
                return
            await self._update_channel_name(new_vc)
            return

        # 2) Suppression du salon temporaire quand il se vide
        if before.channel and before.channel.id in TEMP_VC_IDS:
            if before.channel.members:
                pass
            else:
                try:
                    await before.channel.delete(reason="Salon temporaire vide")
                except discord.HTTPException:
                    logger.exception(
                        "Suppression du salon %s échouée", before.channel.id
                    )
                else:
                    task = self._rename_tasks.pop(before.channel.id, None)
                    if task:
                        task.cancel()
                    logger.info(
                        "[temp_vc] deleted temporary channel '%s' (ID %s) after %s (%s) left",
                        before.channel.name,
                        before.channel.id,
                        member,
                        member.id,
                    )
                    TEMP_VC_IDS.discard(before.channel.id)
                    self._last_names.pop(before.channel.id, None)
                    save_temp_vc_ids(TEMP_VC_IDS)

        # 3) Renommage sur changement d'état vocal
        if after.channel and after.channel.id in TEMP_VC_IDS:
            if not before.channel or before.channel.id != after.channel.id:
                logger.info(
                    "[temp_vc] %s (%s) joined temporary channel '%s' (ID %s)",
                    member,
                    member.id,
                    after.channel.name,
                    after.channel.id,
                )
            await self._update_channel_name(after.channel)
        if (
            before.channel
            and before.channel != after.channel
            and before.channel.id in TEMP_VC_IDS
        ):
            await self._update_channel_name(before.channel)

    @commands.Cog.listener()
    async def on_presence_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """Renomme le salon quand un membre commence/arrête un jeu."""
        if after.voice and after.voice.channel and after.voice.channel.id in TEMP_VC_IDS:
            await self._update_channel_name(after.voice.channel)

    # ---------- tâche de nettoyage ----------

    @tasks.loop(seconds=TEMP_VC_CHECK_INTERVAL_SECONDS)
    async def cleanup(self) -> None:
        for channel_id in list(TEMP_VC_IDS):
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                await self._update_channel_name(channel)
        await delete_untracked_temp_vcs(self.bot, TEMP_VC_CATEGORY, TEMP_VC_IDS)
        save_temp_vc_ids(TEMP_VC_IDS)

    @cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVCCog(bot))
