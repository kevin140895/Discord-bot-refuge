"""Mise à jour des salons de statistiques du serveur.

La cog renomme périodiquement les canaux affichant le nombre de membres,
les utilisateurs en ligne et l'activité vocale. Les valeurs sont
maintenant persistées dans ``stats_cache.json`` pour survivre aux
redémarrages.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Dict

import discord
from discord.ext import commands, tasks

import config
from config import DATA_DIR
from utils.metrics import measure
from utils.persistence import atomic_write_json_async, ensure_dir, read_json_safe
from utils.rename_manager import rename_manager

logger = logging.getLogger(__name__)

STATS_CACHE_FILE = Path(DATA_DIR) / "stats_cache.json"
ensure_dir(STATS_CACHE_FILE.parent)


async def _ensure_rename_manager_started() -> None:
    """Start ``rename_manager`` if its worker is inactive."""
    worker = getattr(rename_manager, "_worker", None)
    if worker is None or worker.done():
        logger.warning("rename_manager inactive; starting worker")
        await rename_manager.start()


class StatsCog(commands.Cog):
    """Gestion des salons de statistiques (membres, activité, etc.)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Cache {guild_id: {"members": int, "online": int, "voice": int}}
        self.cache: Dict[str, Dict[str, int]] = read_json_safe(STATS_CACHE_FILE) or {}
        # Lancement différé pour appliquer le cache avant les boucles
        self._startup_task = asyncio.create_task(self._startup())

    async def _startup(self) -> None:
        try:
            await self.bot.wait_until_ready()
            await self._apply_cache()
            for guild in self.bot.guilds:
                await self.update_members(guild)
                await self.update_online(guild)
                await self.update_voice(guild)
            self.refresh_members.start()
            self.refresh_online.start()
            self.refresh_voice.start()
        except asyncio.CancelledError:  # pragma: no cover - task cancellation
            raise
        except Exception:  # pragma: no cover - unexpected errors
            logger.exception("Erreur inattendue lors du démarrage de StatsCog")

    async def _apply_cache(self) -> None:
        await _ensure_rename_manager_started()
        for guild in self.bot.guilds:
            gid = str(getattr(guild, "id", 0))
            data = self.cache.get(gid)
            if not data:
                continue
            if "members" in data:
                channel = guild.get_channel(config.STATS_MEMBERS_CHANNEL_ID)
                if channel is not None:
                    await rename_manager.request(
                        channel, f"👥 Membres : {data['members']}"
                    )
            if "online" in data:
                channel = guild.get_channel(config.STATS_ONLINE_CHANNEL_ID)
                if channel is not None:
                    await rename_manager.request(
                        channel, f"🟢 En ligne : {data['online']}"
                    )
            if "voice" in data:
                channel = guild.get_channel(config.STATS_VOICE_CHANNEL_ID)
                if channel is not None:
                    await rename_manager.request(
                        channel, f"🔊 Voc : {data['voice']}"
                    )

    def cog_unload(self) -> None:
        self.refresh_members.cancel()
        self.refresh_online.cancel()
        self.refresh_voice.cancel()
        self._startup_task.cancel()

    async def update_members(self, guild: discord.Guild) -> None:
        """Met à jour le nombre de membres pour ``guild``."""
        await _ensure_rename_manager_started()
        with measure("stats.update_members"):
            members = guild.member_count - sum(
                1 for m in guild.members if getattr(m, "bot", False)
            )
            channel = guild.get_channel(config.STATS_MEMBERS_CHANNEL_ID)
            if channel is not None:
                await rename_manager.request(
                    channel, f"👥 Membres : {members}"
                )
            gid = str(getattr(guild, "id", 0))
            self.cache.setdefault(gid, {})["members"] = members
            await atomic_write_json_async(STATS_CACHE_FILE, self.cache)

    async def update_online(self, guild: discord.Guild) -> None:
        """Met à jour le nombre d'utilisateurs en ligne pour ``guild``."""
        await _ensure_rename_manager_started()
        with measure("stats.update_online"):
            online = sum(
                1
                for m in guild.members
                if not getattr(m, "bot", False) and m.status != discord.Status.offline
            )
            channel = guild.get_channel(config.STATS_ONLINE_CHANNEL_ID)
            if channel is not None:
                await rename_manager.request(
                    channel, f"🟢 En ligne : {online}"
                )
            gid = str(getattr(guild, "id", 0))
            self.cache.setdefault(gid, {})["online"] = online
            await atomic_write_json_async(STATS_CACHE_FILE, self.cache)

    async def update_voice(self, guild: discord.Guild) -> None:
        """Met à jour le nombre d'utilisateurs en vocal pour ``guild``."""
        await _ensure_rename_manager_started()
        with measure("stats.update_voice"):
            voice = sum(
                len([m for m in vc.members if not getattr(m, "bot", False)])
                for vc in getattr(guild, "voice_channels", [])
            )
            channel = guild.get_channel(config.STATS_VOICE_CHANNEL_ID)
            if channel is not None:
                await rename_manager.request(
                    channel, f"🔊 Voc : {voice}"
                )
            gid = str(getattr(guild, "id", 0))
            self.cache.setdefault(gid, {})["voice"] = voice
            await atomic_write_json_async(STATS_CACHE_FILE, self.cache)

    @tasks.loop(time=time(hour=0))
    async def refresh_members(self) -> None:
        """Met à jour le nombre de membres une fois par jour."""
        await self.bot.wait_until_ready()
        # Reset du cache au début de chaque mois
        if datetime.utcnow().day == 1:
            for p in [STATS_CACHE_FILE, STATS_CACHE_FILE.with_suffix(STATS_CACHE_FILE.suffix + ".bak")]:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            self.cache.clear()
        for guild in self.bot.guilds:
            try:
                await self.update_members(guild)
            except Exception:
                logger.exception("Erreur refresh_members")

    @tasks.loop(minutes=15)
    async def refresh_online(self) -> None:
        """Met à jour le nombre d'utilisateurs en ligne toutes les 15 minutes."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self.update_online(guild)
            except Exception:
                logger.exception("Erreur refresh_online")

    @tasks.loop(minutes=3)
    async def refresh_voice(self) -> None:
        """Met à jour le nombre d'utilisateurs en vocal toutes les 3 minutes."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self.update_voice(guild)
            except Exception:
                logger.exception("Erreur refresh_voice")


async def setup(bot: commands.Bot) -> None:
    await rename_manager.start()
    await bot.add_cog(StatsCog(bot))
