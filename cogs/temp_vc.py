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
)
from storage.temp_vc_store import load_temp_vc_ids, save_temp_vc_ids
from utils.temp_vc_cleanup import delete_untracked_temp_vcs
from utils.discord_utils import safe_channel_edit

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
        self.cleanup.start()
        self._rename_tasks: Dict[int, asyncio.Task] = {}

    def cog_unload(self) -> None:
        self.cleanup.cancel()
        for task in self._rename_tasks.values():
            task.cancel()
        self._rename_tasks.clear()

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
        status = "Chat"
        for m in channel.members:
            if m.voice and m.voice.self_mute:
                status = "Endormie"
                break
            for act in m.activities:
                if isinstance(act, discord.Game):
                    status = act.name
                    break
            if status not in {"Chat", "Endormie"}:
                break
        return f"{base} • {status}"

    async def _rename_channel(self, channel: discord.VoiceChannel) -> None:
        """Tâche différée effectuant le renommage du salon."""
        try:
            await asyncio.sleep(5)
            task = asyncio.current_task()
            if self._rename_tasks.get(channel.id) is not task:
                return
            new = self._compute_channel_name(channel)
            if new and channel.name != new:
                try:
                    await safe_channel_edit(channel, name=new)
                except discord.HTTPException:
                    logging.exception("Renommage du salon %s échoué", channel.id)
        except asyncio.CancelledError:
            pass
        finally:
            if self._rename_tasks.get(channel.id) is asyncio.current_task():
                self._rename_tasks.pop(channel.id, None)

    async def _update_channel_name(self, channel: discord.VoiceChannel) -> None:
        """Programme ou reprogramme le renommage du salon après un délai."""
        task = self._rename_tasks.get(channel.id)
        if task:
            task.cancel()
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
            try:
                await member.move_to(new_vc)
            except discord.HTTPException:
                pass
            await self._update_channel_name(new_vc)
            return

        # 2) Suppression du salon temporaire quand il se vide
        if before.channel and before.channel.id in TEMP_VC_IDS:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Salon temporaire vide")
                except discord.HTTPException:
                    logging.exception(
                        "Suppression du salon %s échouée", before.channel.id
                    )
                else:
                    TEMP_VC_IDS.discard(before.channel.id)
                    save_temp_vc_ids(TEMP_VC_IDS)

        # 3) Renommage sur changement d'état vocal
        if after.channel and after.channel.id in TEMP_VC_IDS:
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

    @tasks.loop(minutes=10)
    async def cleanup(self) -> None:
        await delete_untracked_temp_vcs(self.bot, TEMP_VC_CATEGORY, TEMP_VC_IDS)
        save_temp_vc_ids(TEMP_VC_IDS)

    @cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVCCog(bot))

