"""Challenge quotidien du premier message."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DATA_DIR
from storage.xp_store import xp_store
from utils.interactions import safe_respond
from utils.persistence import (
    atomic_write_json_async,
    ensure_dir,
    read_json_safe,
)

FIRST_WIN_FILE = os.path.join(DATA_DIR, "first_win.json")
ensure_dir(DATA_DIR)


class FirstMessageCog(commands.Cog):
    """Gère l'attribution d'XP au premier message de la journée."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise le cog et charge l'état persistant."""
        self.bot = bot
        self.first_message_claimed: bool = True
        self.winner_id: int | None = None
        self._lock = asyncio.Lock()
        self._load_state()
        self.daily_reset.start()

    def cog_unload(self) -> None:
        """Annule les tâches lorsque le cog est déchargé."""
        self.daily_reset.cancel()

    # ── State management ────────────────────────────────────
    def _load_state(self) -> None:
        """Charge l'état depuis le stockage persistant."""
        data = read_json_safe(FIRST_WIN_FILE)
        self.winner_id = data.get("winner_id")
        stored_date = data.get("date")
        now = datetime.now()
        today = now.date().isoformat()
        if now.time() >= time(hour=8):
            if stored_date == today:
                self.first_message_claimed = self.winner_id is not None
            else:
                self._reset_state()
        else:
            self.first_message_claimed = True

    async def _save_state(self) -> None:
        """Sauvegarde l'état actuel dans le fichier JSON."""
        payload = {"date": date.today().isoformat(), "winner_id": self.winner_id}
        await atomic_write_json_async(FIRST_WIN_FILE, payload)

    def _reset_state(self) -> None:
        """Réinitialise le challenge pour une nouvelle journée."""
        self.first_message_claimed = False
        self.winner_id = None
        asyncio.create_task(self._save_state())
        logging.info("[FirstMessage] Challenge réinitialisé")

    # ── Tasks ────────────────────────────────────────────────
    @tasks.loop(time=time(hour=8))
    async def daily_reset(self) -> None:
        """Réinitialise automatiquement le challenge chaque jour à 8h."""
        self._reset_state()

    @daily_reset.before_loop
    async def before_daily_reset(self) -> None:
        """Attend que le bot soit prêt avant de lancer la tâche de reset."""
        await self.bot.wait_until_ready()

    # ── Events ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Récompense le premier message posté après 8h."""
        if message.author.bot or message.guild is None:
            return
        if datetime.now().time() < time(hour=8):
            return
        async with self._lock:
            if self.first_message_claimed:
                return
            self.first_message_claimed = True
            self.winner_id = message.author.id
        old_lvl, new_lvl, total_xp = await xp_store.add_xp(message.author.id, 1000)
        if new_lvl > old_lvl:
            await self.bot.announce_level_up(
                message.guild, message.author, old_lvl, new_lvl, total_xp
            )
        await message.channel.send(
            f"🎉 Félicitations {message.author.mention}, tu es le premier de la journée et tu gagnes 1000 XP !",
        )
        await self._save_state()
        logging.info(
            "[FirstMessage] %s a gagné le challenge du premier message", message.author
        )

    # ── Commands ─────────────────────────────────────────────
    @app_commands.command(name="xpreset", description="Réinitialise le challenge du premier message.")
    @app_commands.checks.has_permissions(administrator=True)
    async def xpreset(self, interaction: discord.Interaction) -> None:
        """Réinitialise manuellement le challenge du jour."""
        self._reset_state()
        await safe_respond(interaction, "Challenge réinitialisé.")

    @app_commands.command(name="xptoday", description="Affiche le gagnant du jour pour le premier message.")
    @app_commands.checks.has_permissions(administrator=True)
    async def xptoday(self, interaction: discord.Interaction) -> None:
        """Affiche le gagnant du jour."""
        if self.winner_id:
            member = interaction.guild.get_member(self.winner_id) if interaction.guild else None
            if member is None and interaction.guild is not None:
                try:
                    member = await interaction.guild.fetch_member(self.winner_id)
                except discord.NotFound:
                    member = None
            mention = member.mention if member else f"<@{self.winner_id}>"
            await safe_respond(
                interaction,
                f"Le gagnant du jour est {mention}.",
            )
        else:
            await safe_respond(
                interaction,
                "Personne n'a encore gagné aujourd'hui.",
            )


async def setup(bot: commands.Bot) -> None:
    """Charge le cog dans le bot."""
    await bot.add_cog(FirstMessageCog(bot))
