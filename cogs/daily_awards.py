"""Annonce des gagnants quotidiens à 00h00."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, time, timezone
from typing import Any, Dict

import discord
from discord.ext import commands

from config import (
    AWARD_ANNOUNCE_CHANNEL_ID,
    GUILD_ID,
    DATA_DIR,
    ENABLE_DAILY_AWARDS,
)
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir

try:  # pragma: no cover - import guard
    from cogs.daily_leaderboard import DAILY_WINNERS_FILE
except Exception:  # pragma: no cover - fallback
    DAILY_WINNERS_FILE = os.path.join(DATA_DIR, "daily_winners.json")

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc

DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
STATE_FILE = os.path.join(DATA_DIR, "daily_awards_state.json")
ensure_dir(DATA_DIR)

_awards_lock: asyncio.Lock = asyncio.Lock()


def mention(uid: int) -> str:
    return f"<@{uid}>"


def fmt_hm(seconds: int) -> str:
    m = max(0, seconds // 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def load_state() -> Dict[str, Any]:
    return read_json_safe(STATE_FILE)


def save_state(date: str | None, message_id: int | None) -> None:
    atomic_write_json(
        STATE_FILE, {"last_posted_date": date, "last_message_id": message_id}
    )


def _normalize_award_payload(data: Dict[str, Any], *, date: str | None = None) -> Dict[str, Any]:
    """Garantit la présence des clés attendues pour l'embed."""

    payload: Dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    top3 = payload.get("top3") or {}
    winners = payload.get("winners") or {}
    payload["top3"] = top3 if isinstance(top3, dict) else {}
    payload["winners"] = winners if isinstance(winners, dict) else {}
    if date and "date" not in payload:
        payload["date"] = date
    return payload


def _load_latest_award_data() -> Dict[str, Any] | None:
    """Charge les derniers gagnants connus.

    Préfère ``daily_winners.json`` mais conserve une compatibilité avec
    ``daily_ranking.json`` si nécessaire.
    """

    winners_data = read_json_safe(DAILY_WINNERS_FILE)
    if isinstance(winners_data, dict) and winners_data:
        try:
            latest_day = max(day for day in winners_data.keys() if isinstance(day, str))
        except ValueError:
            latest_day = None
        if latest_day:
            yesterday = (datetime.now(PARIS_TZ).date() - timedelta(days=1)).isoformat()
            payload = winners_data.get(latest_day)
            if latest_day == yesterday and isinstance(payload, dict):
                # ``daily_winners.json`` n'est fiable que lorsque l'entrée d'hier a
                # été générée par la remise à zéro du classement.
                return _normalize_award_payload(payload, date=latest_day)

    legacy_data = read_json_safe(DAILY_RANK_FILE)
    if isinstance(legacy_data, dict) and legacy_data:
        date = legacy_data.get("date") if isinstance(legacy_data.get("date"), str) else None
        return _normalize_award_payload(legacy_data, date=date)

    return None


def today_str_eu_paris() -> str:
    """Retourne la date du jour au format YYYY-MM-DD en Europe/Paris."""
    return datetime.now(PARIS_TZ).date().isoformat()


class DailyAwards(commands.Cog):
    """Publie l'annonce des gagnants."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task = None
        if ENABLE_DAILY_AWARDS:
            self._task = asyncio.create_task(self._scheduler())
            asyncio.create_task(self._startup_check())

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        if self._task:
            self._task.cancel()

    # ── Persistence helpers ──────────────────────────────────
    def _read_state(self) -> Dict[str, Any]:
        return load_state()

    def _write_state(self, data: Dict[str, Any]) -> None:
        save_state(data.get("last_posted_date"), data.get("last_message_id"))

    async def _get_announce_channel(self) -> discord.abc.Messageable | None:
        """Récupère le salon d'annonce en vérifiant les permissions."""
        channel = self.bot.get_channel(AWARD_ANNOUNCE_CHANNEL_ID)
        guild = self.bot.get_guild(GUILD_ID) if hasattr(self.bot, "get_guild") else None
        if channel is None and guild:
            try:
                channel = await guild.fetch_channel(AWARD_ANNOUNCE_CHANNEL_ID)
            except discord.Forbidden:
                logger.warning(
                    "[daily_awards] Accès refusé au salon %s", AWARD_ANNOUNCE_CHANNEL_ID
                )
            except discord.NotFound:
                logger.warning(
                    "[daily_awards] Salon %s introuvable", AWARD_ANNOUNCE_CHANNEL_ID
                )
            except discord.HTTPException:
                logger.exception(
                    "[daily_awards] Erreur HTTP lors de la récupération du salon %s",
                    AWARD_ANNOUNCE_CHANNEL_ID,
                )
        if channel and hasattr(channel, "send"):
            me = None
            if guild:
                me = getattr(guild, "me", None)
            elif hasattr(channel, "guild"):
                me = getattr(channel.guild, "me", None)
            if me and hasattr(channel, "permissions_for"):
                perms = channel.permissions_for(me)
                if not perms.send_messages:
                    logger.warning(
                        "[daily_awards] Pas la permission d'envoyer dans %s",
                        getattr(channel, "id", "inconnu"),
                    )
                    channel = None
            if channel:
                return channel
        if guild:
            fallback = next(
                (
                    c
                    for c in getattr(guild, "text_channels", [])
                    if c.permissions_for(guild.me).send_messages
                ),
                None,
            )
            if fallback:
                logger.warning(
                    "[daily_awards] Utilisation du salon de secours %s", fallback.id
                )
                return fallback
        logger.error("[daily_awards] Aucun salon texte disponible pour l'annonce")
        return None

    async def _build_embed(self, data: Dict[str, Any]) -> discord.Embed:
        top3 = data.get("top3", {})
        mvp = top3.get("mvp") or []
        writer = top3.get("msg") or []
        voice = top3.get("vc") or []

        embed = discord.Embed(
            title="📢 Annonce des gagnants — classement de 00h00",
            colour=0xFF1801,
        )

        if mvp:
            m = mvp[0]
            value = (
                f"{mention(m['id'])}\n"
                f"Points combinés : {float(m['score']):.2f} "
                f"(messages : {m['messages']} · vocal : {fmt_hm(m['voice'] * 60)})"
            )
        else:
            value = "— Aucun gagnant aujourd’hui —"
        embed.add_field(name="MVP", value=value, inline=False)

        if writer:
            w = writer[0]
            value = f"{mention(w['id'])}\nMessages envoyés : {w['count']}"
        else:
            value = "— Aucun gagnant aujourd’hui —"
        embed.add_field(name="Écrivain", value=value, inline=False)

        if voice:
            v = voice[0]
            value = f"{mention(v['id'])}\nTemps en vocal : {fmt_hm(v['minutes'] * 60)}"
        else:
            value = "— Aucun gagnant aujourd’hui —"
        embed.add_field(name="Voix", value=value, inline=False)

        embed.set_footer(
            text=f"Date : {datetime.now(PARIS_TZ).strftime('%d/%m/%Y')}"
        )
        return embed

    async def _maybe_award(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        payload = _normalize_award_payload(data)
        today = today_str_eu_paris()
        logger.info("[daily_awards] Début annonce du %s", today)

        async with _awards_lock:
            state = self._read_state()

            channel = await self._get_announce_channel()
            if channel is None:
                return

            embed = await self._build_embed(payload)

            last_date = state.get("last_posted_date")
            last_id = state.get("last_message_id")

            if last_date == today and last_id:
                try:
                    msg = await channel.fetch_message(last_id)
                except discord.NotFound:
                    logger.warning(
                        "[daily_awards] Message %s introuvable, nouvel envoi", last_id
                    )
                except discord.HTTPException:
                    logger.exception(
                        "[daily_awards] Erreur HTTP lors de la récupération du message %s",
                        last_id,
                    )
                    return
                else:
                    if msg.embeds and msg.embeds[0].to_dict() == embed.to_dict():
                        logger.info("[daily_awards] Annonce déjà à jour pour aujourd'hui")
                        return
                    try:
                        await msg.edit(embed=embed)
                        logger.info("[daily_awards] Annonce %s mise à jour", today)
                    except discord.HTTPException:
                        logger.exception(
                            "[daily_awards] Échec de l'édition de l'annonce"
                        )
                    return

            try:
                msg = await channel.send(
                    "@everyone",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.Forbidden:
                logger.warning(
                    "[daily_awards] Permissions insuffisantes pour envoyer l'annonce"
                )
                return
            except discord.NotFound:
                logger.warning(
                    "[daily_awards] Salon introuvable lors de l'envoi de l'annonce"
                )
                return
            except discord.HTTPException:
                logger.exception(
                    "[daily_awards] Erreur HTTP lors de l'envoi de l'annonce"
                )
                return

            self._write_state(
                {
                    "last_posted_date": today,
                    "last_message_id": getattr(msg, "id", None),
                }
            )
            logger.info("[daily_awards] Annonce %s publiée", today)

    # ── Tasks ────────────────────────────────────────────────
    async def _scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(PARIS_TZ)
            target = datetime.combine(now.date(), time(hour=0, tzinfo=PARIS_TZ))
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            for _ in range(10):
                data = _load_latest_award_data()
                if data and data.get("winners"):
                    try:
                        await self._maybe_award(data)
                    except Exception:
                        logger.exception("[daily_awards] Échec de _maybe_award")
                    break

    async def _startup_check(self) -> None:
        await self.bot.wait_until_ready()
        # Au démarrage, ``daily_ranking`` peut encore être en train de
        # calculer le classement précédent. Pour éviter de rater
        # l'annonce, on patiente quelques instants et on réessaie tant que
        # le fichier de classement ne contient pas les gagnants attendus.
        for _ in range(5):
            data = _load_latest_award_data()
            if data and data.get("winners"):
                await self._maybe_award(data)
                return
            await asyncio.sleep(2)
        logger.warning("[daily_awards] Classement introuvable au démarrage")


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyAwards(bot))
