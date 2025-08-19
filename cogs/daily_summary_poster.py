"""Publication quotidienne du classement.

Cette cog lit les classements calculés par :mod:`cogs.daily_ranking`
et les publie chaque jour à 00h02 dans le salon dédié. Un fichier de
persistance est utilisé pour éviter la publication multiple du même
classement. Une commande slash permet également de prévisualiser le
message envoyé.
"""

from __future__ import annotations

import logging
import os
from datetime import time, timezone
from typing import Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ACTIVITY_SUMMARY_CH, DATA_DIR, XP_VIEWER_ROLE_ID
from utils.interactions import safe_respond
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc


DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
DAILY_SUMMARY_FILE = os.path.join(DATA_DIR, "daily_summary.json")
ensure_dir(DATA_DIR)


def _format_hm(minutes: int) -> str:
    """Format ``minutes`` as ``HhMM``."""

    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}"


class DailySummaryPoster(commands.Cog):
    """Publie le classement quotidien à 00h02."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.daily_task.start()
        self.bot.loop.create_task(self._startup_check())

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        self.daily_task.cancel()

    # ── Persistence helpers ──────────────────────────────────
    def _read_summary(self) -> Dict[str, Any]:
        return read_json_safe(DAILY_SUMMARY_FILE)

    def _write_summary(self, data: Dict[str, Any]) -> None:
        atomic_write_json(DAILY_SUMMARY_FILE, data)

    # ── Message formatting ───────────────────────────────────
    def _build_message(self, data: Dict[str, Any]) -> str:
        top3 = data.get("top3", {})

        medals = ["🥇", "🥈", "🥉"]

        def fmt_msg(idx: int) -> str:
            if idx < len(top3.get("msg", [])):
                entry = top3["msg"][idx]
                return f"{medals[idx]} <@{entry['id']}> — {entry['count']} msgs"
            return f"{medals[idx]} —"

        def fmt_vc(idx: int) -> str:
            if idx < len(top3.get("vc", [])):
                entry = top3["vc"][idx]
                hm = _format_hm(entry["minutes"])
                return f"{medals[idx]} <@{entry['id']}> — {hm}"
            return f"{medals[idx]} —"

        def fmt_mvp(idx: int) -> str:
            if idx < len(top3.get("mvp", [])):
                entry = top3["mvp"][idx]
                return f"{medals[idx]} <@{entry['id']}> — score {entry['score']}"
            return f"{medals[idx]} —"

        lines = [
            "📊 Classement journalier du Refuge 📊",
            "",
            "**Messages envoyés**",
            fmt_msg(0),
            fmt_msg(1),
            fmt_msg(2),
            "",
            "**Temps vocal**",
            fmt_vc(0),
            fmt_vc(1),
            fmt_vc(2),
            "",
            "**MVP (messages + vocal)**",
            fmt_mvp(0),
            fmt_mvp(1),
            fmt_mvp(2),
            "",
            "🔄 Les rôles journaliers ont été remis à zéro, et nos champions gardent leur titre jusqu’à **23h59** !",
            "👉 Continuez à être actifs pour grimper au classement de demain 💬🎤",
        ]
        return "\n".join(lines)

    # ── Core logic ───────────────────────────────────────────
    async def _maybe_post(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        summary = self._read_summary()
        channel = self.bot.get_channel(ACTIVITY_SUMMARY_CH)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(ACTIVITY_SUMMARY_CH)
            except Exception:
                logging.exception(
                    "[daily_summary] Salon %s introuvable", ACTIVITY_SUMMARY_CH
                )
                self._write_summary(
                    {"date": data.get("date"), "error": "channel_not_found"}
                )
                return

        message_id = summary.get("message_id")
        if summary.get("date") == data.get("date") and message_id:
            try:
                await channel.fetch_message(message_id)
                return  # already posted and message exists
            except discord.NotFound:
                logging.warning(
                    "[daily_summary] Message %s introuvable, re-publication", message_id
                )

        message = self._build_message(data)
        msg = await channel.send(message)
        self._write_summary({"date": data.get("date"), "message_id": msg.id})
        logging.info("[daily_summary] Classement %s publié", data.get("date"))

    # ── Tasks ────────────────────────────────────────────────
    @tasks.loop(time=time(hour=0, minute=2, tzinfo=PARIS_TZ))
    async def daily_task(self) -> None:
        await self.bot.wait_until_ready()
        data = read_json_safe(DAILY_RANK_FILE)
        await self._maybe_post(data)

    @daily_task.before_loop
    async def before_daily_task(self) -> None:  # pragma: no cover - scheduler hook
        await self.bot.wait_until_ready()

    async def _startup_check(self) -> None:
        await self.bot.wait_until_ready()
        data = read_json_safe(DAILY_RANK_FILE)
        await self._maybe_post(data)

    # ── Slash command -------------------------------------------------
    @app_commands.command(
        name="test_classement2", description="Prévisualise le message du jour"
    )
    async def test_classement2(
        self, interaction: discord.Interaction
    ) -> None:
        if not any(r.id == XP_VIEWER_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            await safe_respond(interaction, "Accès refusé.", ephemeral=True)
            return
        data = read_json_safe(DAILY_RANK_FILE)
        if not data:
            await safe_respond(interaction, "Aucun classement disponible.", ephemeral=True)
            return
        message = self._build_message(data)
        await safe_respond(interaction, message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailySummaryPoster(bot))

