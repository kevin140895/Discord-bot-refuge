"""Challenge quotidien du premier message."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time

import discord
from discord.ext import commands, tasks

from config import DATA_DIR
from storage.xp_store import xp_store
from utils.persistence import (
    atomic_write_json_async,
    ensure_dir,
    read_json_safe,
)
logger = logging.getLogger(__name__)

FIRST_WIN_FILE = os.path.join(DATA_DIR, "first_win.json")
ensure_dir(DATA_DIR)


class FirstMessageCog(commands.Cog):
    """Gère l'attribution d'XP au premier message de la journée."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise le cog et charge l'état persistant."""
        self.bot = bot
        self.first_message_claimed: bool = True
        self.winner_id: int | None = None
        self.claimed_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._save_task: asyncio.Task | None = None
        self._load_state()
        self.daily_reset.start()

    def cog_unload(self) -> None:
        """Annule les tâches lorsque le cog est déchargé."""
        self.daily_reset.cancel()
        if self._save_task is not None:
            if self._save_task.done():
                try:
                    self._save_task.result()
                except Exception:  # pragma: no cover - logging
                    logger.exception("[FirstMessage] Échec de la sauvegarde d'état")
            else:
                self._save_task.cancel()

    # ── State management ────────────────────────────────────
    def _load_state(self) -> None:
        """Charge l'état depuis le stockage persistant."""
        data = read_json_safe(FIRST_WIN_FILE)
        self.winner_id = data.get("winner_id")
        claimed_at_raw = data.get("claimed_at")
        try:
            self.claimed_at = (
                datetime.fromisoformat(claimed_at_raw) if claimed_at_raw else None
            )
        except ValueError:
            self.claimed_at = None

        now = datetime.now()
        if now.time() < time(hour=8):
            # Le challenge n'est actif qu'après 8h.
            self.first_message_claimed = True
            return

        if self.claimed_at and self.claimed_at.date() == now.date():
            # Récompense déjà attribuée aujourd'hui.
            self.first_message_claimed = True
        else:
            self._reset_state()

    async def _save_state(self) -> None:
        """Sauvegarde l'état actuel dans le fichier JSON."""
        payload = {
            "date": date.today().isoformat(),
            "winner_id": self.winner_id,
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at else None,
        }
        await atomic_write_json_async(FIRST_WIN_FILE, payload)

    def _reset_state(self) -> None:
        """Réinitialise le challenge pour une nouvelle journée."""
        self.first_message_claimed = False
        self.winner_id = None
        self.claimed_at = None
        task = asyncio.create_task(self._save_state(), name="first_message_save")
        task.add_done_callback(self._handle_save_task_result)
        self._save_task = task
        logger.info("[FirstMessage] Challenge réinitialisé")

    def _handle_save_task_result(self, task: asyncio.Task) -> None:
        """Log les erreurs potentielles de la tâche de sauvegarde."""
        try:
            task.result()
        except Exception:  # pragma: no cover - logging
            logger.exception("[FirstMessage] Erreur lors de la sauvegarde de l'état")

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
            self.claimed_at = datetime.now()
        old_lvl, new_lvl, old_xp, total_xp = await xp_store.add_xp(
            message.author.id,
            400,
            guild_id=message.guild.id if message.guild else 0,
            source="message",
        )
        await message.channel.send(
            f"🎉 Félicitations {message.author.mention}, tu es le premier de la journée et tu gagnes 400 XP !",
        )
        await self._save_state()
        logger.info(
            "[FirstMessage] %s a gagné le challenge du premier message", message.author
        )

    # ── Commands ─────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    """Charge le cog dans le bot."""
    await bot.add_cog(FirstMessageCog(bot))
