"""Gestion du classement quotidien et attribution des rôles associés.

Ce module calcule les gagnants quotidiens à partir des statistiques
d'activité et persiste les résultats dans ``daily_ranking.json`` via
``utils.persistence``. Il lit et écrit également les statistiques
journalières partagées avec la cog XP.
"""

import logging
import os
from datetime import datetime, timedelta, time, timezone
from typing import Dict, Any
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    MVP_ROLE_ID,
    TOP_MSG_ROLE_ID,
    TOP_VC_ROLE_ID,
    XP_VIEWER_ROLE_ID,
)
from utils.interactions import safe_respond
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir
from .xp import DAILY_STATS, DAILY_LOCK, save_daily_stats_to_disk

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc


DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
ensure_dir(DATA_DIR)


class DailyRankingAndRoles(commands.Cog):
    """Calcul et attribution des classements quotidiens."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.daily_task.start()
        self.bot.loop.create_task(self._startup_apply())

    def cog_unload(self) -> None:
        self.daily_task.cancel()

    async def _startup_apply(self) -> None:
        await self.bot.wait_until_ready()
        await self._apply_roles_from_file()

    # ── Persistence helpers ──────────────────────────────────

    def _read_persistence(self) -> Dict[str, Any]:
        return read_json_safe(DAILY_RANK_FILE)

    def _write_persistence(self, data: Dict[str, Any]) -> None:
        atomic_write_json(DAILY_RANK_FILE, data)

    # ── Roles management ─────────────────────────────────────

    async def _apply_roles_from_file(self) -> None:
        data = self._read_persistence()
        winners = data.get("winners") if data else {}
        if winners:
            logging.info("[daily_ranking] Réapplication des rôles pour %s", data.get("date"))
            await self._reset_and_assign(winners)

    async def _reset_and_assign(self, winners: Dict[str, int | None]) -> None:
        guild = self.bot.guilds[0] if self.bot.guilds else None
        if not guild:
            return
        roles = {
            "mvp": guild.get_role(MVP_ROLE_ID),
            "msg": guild.get_role(TOP_MSG_ROLE_ID),
            "vc": guild.get_role(TOP_VC_ROLE_ID),
        }
        for member in guild.members:
            to_remove = [r for r in roles.values() if r and r in member.roles]
            if to_remove:
                try:
                    await member.remove_roles(
                        *to_remove,
                        reason="Réinitialisation des rôles journaliers",
                    )
                except discord.Forbidden:
                    logging.warning(
                        "[daily_ranking] Permissions insuffisantes pour retirer un rôle"
                    )
                except discord.NotFound:
                    logging.warning(
                        "[daily_ranking] Rôle ou membre introuvable lors du retrait"
                    )
                except discord.HTTPException as e:
                    logging.error(
                        "[daily_ranking] Erreur HTTP lors du retrait d'un rôle: %s",
                        e,
                    )
                except Exception as e:  # pragma: no cover - just log
                    logging.exception(
                        "[daily_ranking] Erreur inattendue lors du retrait d'un rôle: %s",
                        e,
                    )
        for key, uid in winners.items():
            role = roles.get(key)
            if not role or not uid:
                continue
            member = guild.get_member(int(uid))
            if not member:
                continue
            try:
                await member.add_roles(
                    role, reason="Attribution classement quotidien"
                )
                logging.info("[daily_ranking] Rôle %s attribué à %s", role.id, uid)
            except discord.Forbidden:
                logging.warning(
                    "[daily_ranking] Permissions insuffisantes pour attribuer un rôle"
                )
            except discord.NotFound:
                logging.warning(
                    "[daily_ranking] Rôle ou membre introuvable lors de l'attribution"
                )
            except discord.HTTPException as e:
                logging.error(
                    "[daily_ranking] Erreur HTTP lors de l'attribution du rôle: %s",
                    e,
                )
            except Exception as e:  # pragma: no cover
                logging.exception(
                    "[daily_ranking] Erreur inattendue lors de l'attribution du rôle: %s",
                    e,
                )

    # ── Computation ──────────────────────────────────────────

    def _compute_ranking(self, stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        msg_sorted = sorted(stats.items(), key=lambda x: x[1].get("messages", 0), reverse=True)
        vc_sorted = sorted(stats.items(), key=lambda x: x[1].get("voice", 0), reverse=True)

        def score(item: tuple[str, Dict[str, int]]) -> float:
            s = item[1]
            return s.get("messages", 0) + s.get("voice", 0) / 60.0

        mvp_sorted = sorted(stats.items(), key=score, reverse=True)

        top_msg = [
            {"id": int(uid), "count": int(data.get("messages", 0))}
            for uid, data in msg_sorted[:3]
        ]
        top_vc = [
            {"id": int(uid), "minutes": int(data.get("voice", 0) // 60)}
            for uid, data in vc_sorted[:3]
        ]
        top_mvp = [
            {"id": int(uid), "score": round(score((uid, data)), 2)}
            for uid, data in mvp_sorted[:3]
        ]

        winners = {
            "msg": top_msg[0]["id"] if top_msg else None,
            "vc": top_vc[0]["id"] if top_vc else None,
            "mvp": top_mvp[0]["id"] if top_mvp else None,
        }
        return {"top3": {"msg": top_msg, "vc": top_vc, "mvp": top_mvp}, "winners": winners}

    # ── Main task ───────────────────────────────────────────

    @tasks.loop(time=time(hour=0, tzinfo=PARIS_TZ))
    async def daily_task(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(PARIS_TZ)
        day = (now - timedelta(days=1)).date().isoformat()
        logging.info("[daily_ranking] Calcul du classement pour %s", day)
        async with DAILY_LOCK:
            stats = DAILY_STATS.pop(day, {})
        if not stats:
            logging.info("[daily_ranking] Aucune statistique pour %s", day)
            await save_daily_stats_to_disk()
            return
        ranking = self._compute_ranking(stats)
        ranking["date"] = day
        self._write_persistence(ranking)
        await save_daily_stats_to_disk()
        await self._reset_and_assign(ranking["winners"])
        logging.info("[daily_ranking] Classement %s sauvegardé et rôles attribués", day)

    @daily_task.before_loop
    async def before_daily_task(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="test_classement1", description="Prévisualise le classement du jour"
    )
    async def test_classement1(
        self, interaction: discord.Interaction
    ) -> None:
        if not any(r.id == XP_VIEWER_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            await safe_respond(interaction, "Accès refusé.", ephemeral=True)
            return
        data = read_json_safe(DAILY_RANK_FILE)
        if not data:
            await safe_respond(interaction, "Aucun classement disponible.", ephemeral=True)
            return
        await safe_respond(
            interaction,
            f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyRankingAndRoles(bot))

