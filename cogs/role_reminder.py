# cogs/role_reminder.py
import asyncio
import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import discord
from discord import app_commands
from discord.ext import commands
from utils.metrics import measure

# ─────────────────────── PARAMS ───────────────────────
try:
    from zoneinfo import ZoneInfo
    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:
    PARIS_TZ = None  # fallback naive UTC si indispo

REMINDER_CHANNEL_ID = 1400552164979507263          # salon unique où on écrit
ROLE_CHOICE_CHANNEL_ID = 1400560866478395512       # salon des boutons de rôles

# Rôles à ignorer (fournis par toi)
IGNORED_ROLE_IDS = {
    1402071696277635157,
    1404054439706234910,
    1403510368340410550,
    1405170057792979025,
    1402302249035894968,
}

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
ROLE_REMINDERS_FILE = f"{DATA_DIR}/role_reminders.json"

SCAN_PERIOD_HOURS = 72
CLEANUP_TICK_MIN = 10
REMINDER_TTL_HOURS = 24

SLEEP_MIN, SLEEP_MAX = 0.8, 1.2  # étalement anti-429

REMINDER_TEMPLATE = (
    "Salut {mention}, pense à choisir tes rôles dans <#{role_choice_ch}>.\n"
    "Ce message sera supprimé si aucun rôle n’est choisi sous 24h."
)

# ─────────────────────── HELPERS ───────────────────────


def _now_tz() -> datetime:
    return datetime.now(PARIS_TZ) if PARIS_TZ else datetime.utcnow()


def _iso(dt: datetime) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_data_dir():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.error(f"[rolescan] JSON corrompu: {path}")
        return {}


def _write_json(path: str, data: Dict[str, Any]):
    _ensure_data_dir()
    try:
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logging.error(f"[rolescan] Écriture JSON échouée pour {path}: {e}")


def user_without_chosen_role(member: discord.Member) -> bool:
    """True si le membre n'a aucun rôle hors @everyone
    et hors IGNORED_ROLE_IDS."""

    for r in member.roles:
        if r.is_default():  # @everyone
            continue
        if r.id in IGNORED_ROLE_IDS:
            continue
        return False
    return True


# ─────────────────────── COG ───────────────────────


class RoleReminderCog(commands.Cog):
    """Rappels 72h pour rôles + purge des rappels à +24h,
    avec persistance JSON."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {guild_id: {user_id: {message_id, channel_id, created_at}}}
        self.reminders: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._load_state()

        self._scan_task = asyncio.create_task(self._scan_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def cog_unload(self):
        self._scan_task.cancel()
        self._cleanup_task.cancel()

    # ── State ──

    def _load_state(self):
        self.reminders = _read_json(ROLE_REMINDERS_FILE) or {}
        # normalisation
        for g in list(self.reminders.keys()):
            for u in list(self.reminders[g].keys()):
                self.reminders[g][u].setdefault(
                    "channel_id", REMINDER_CHANNEL_ID
                )

    def _save_state(self):
        _write_json(ROLE_REMINDERS_FILE, self.reminders)

    # ── Loops ──

    async def _scan_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # laisse le cache de membres arriver
        while not self.bot.is_closed():
            try:
                await self._run_scan_once()
            except Exception as e:
                logging.exception(f"[rolescan] erreur scan: {e}")
            await asyncio.sleep(SCAN_PERIOD_HOURS * 3600)

    async def _cleanup_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._run_cleanup_tick()
            except Exception as e:
                logging.exception(f"[rolescan] erreur cleanup: {e}")
            await asyncio.sleep(CLEANUP_TICK_MIN * 60)

    # ── Core actions ──

    async def _run_scan_once(
        self,
        *,
        invoked_by_cmd: bool = False,
        guild: discord.Guild | None = None,
    ):
        with measure("rolescan.scan_once"):
            now = _now_tz()
            guilds = [guild] if guild else list(self.bot.guilds)
            logging.info(
                f"[rolescan] ▶ Début scan (invoked_by_cmd={invoked_by_cmd})"
            )

            for g in guilds:
                if not g:
                    continue

                ch = g.get_channel(REMINDER_CHANNEL_ID)
                if not isinstance(ch, discord.TextChannel):
                    logging.warning(
                        f"[rolescan] Salon {REMINDER_CHANNEL_ID} introuvable "
                        f"(guild {g.id})"
                    )
                    continue

                me = g.me or g.get_member(self.bot.user.id)
                if not me:
                    continue
                perms = ch.permissions_for(me)
                if not (
                    perms.send_messages
                    and perms.manage_messages
                    and perms.read_message_history
                ):
                    logging.warning(
                        f"[rolescan] Permissions insuffisantes dans {ch.id} "
                        f"(guild {g.id})"
                    )
                    continue

                g_key = str(g.id)
                self.reminders.setdefault(g_key, {})
                sent = 0

                for member in g.members:
                    if member.bot:
                        continue
                    if not user_without_chosen_role(member):
                        continue

                    u_key = str(member.id)
                    rec = self.reminders[g_key].get(u_key)
                    if rec:
                        try:
                            created_at = datetime.fromisoformat(
                                rec.get("created_at")
                            )
                        except Exception:
                            created_at = now
                        age_h = (now - created_at).total_seconds() / 3600
                        if age_h < REMINDER_TTL_HOURS:
                            # rappel récent <24h → pas de repost
                            continue

                    # envoyer le rappel
                    try:
                        text = REMINDER_TEMPLATE.format(
                            mention=member.mention,
                            role_choice_ch=ROLE_CHOICE_CHANNEL_ID,
                        )
                        msg = await ch.send(
                            text,
                            allowed_mentions=discord.AllowedMentions(
                                users=True,
                                roles=False,
                                everyone=False,
                            ),
                        )
                        self.reminders[g_key][u_key] = {
                            "message_id": msg.id,
                            "channel_id": ch.id,
                            "created_at": _iso(now),
                        }
                        self._save_state()
                        sent += 1
                        await asyncio.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
                    except Exception as e:
                        logging.error(
                            f"[rolescan] Envoi rappel échoué pour {member}: {e}"
                        )

                logging.info(
                    f"[rolescan] Guild {g.id} — rappels envoyés: {sent}"
                )

            logging.info("[rolescan] ■ Fin scan")

    async def _run_cleanup_tick(self):
        with measure("rolescan.cleanup_tick"):
            now = _now_tz()
            # (channel, msg_id, g_key, u_key)
            to_delete: list[tuple[discord.TextChannel, int, str, str]] = []

            for g in self.bot.guilds:
                g_key = str(g.id)
                g_map = self.reminders.get(g_key, {})
                if not g_map:
                    continue

                ch = g.get_channel(REMINDER_CHANNEL_ID)
                if not isinstance(ch, discord.TextChannel):
                    continue

                for u_key, rec in list(g_map.items()):
                    try:
                        created_at = datetime.fromisoformat(
                            rec.get("created_at")
                        )
                    except Exception:
                        created_at = now
                    age_h = (now - created_at).total_seconds() / 3600
                    if age_h < REMINDER_TTL_HOURS:
                        continue  # pas encore l'heure du check 24h

                    member = g.get_member(int(u_key))
                    if member and not user_without_chosen_role(member):
                        # a pris un rôle → on purge l’état
                        # (message laissé tel quel si déjà supprimé)
                        g_map.pop(u_key, None)
                        continue

                    # toujours sans rôle → suppression du message enregistré
                    msg_id = rec.get("message_id")
                    to_delete.append((ch, int(msg_id), g_key, u_key))

            for ch, msg_id, g_key, u_key in to_delete:
                try:
                    msg = await ch.fetch_message(msg_id)
                except Exception:
                    msg = None
                try:
                    if msg:
                        await msg.delete()
                    self.reminders.get(g_key, {}).pop(u_key, None)
                    self._save_state()
                    await asyncio.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
                except Exception as e:
                    logging.error(
                        f"[rolescan] Suppression message {msg_id} échouée: {e}"
                    )

    # ── Slash commands admin ──

    group = app_commands.Group(
        name="rolescan",
        description="Gestion des rappels de rôles",
    )

    @group.command(
        name="now",
        description="Lancer un scan immédiat",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolescan_now(self, interaction: discord.Interaction):
        with measure("slash:rolescan_now"):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._run_scan_once(invoked_by_cmd=True, guild=interaction.guild)
            await interaction.followup.send("✅ Scan lancé.", ephemeral=True)

    @group.command(
        name="status",
        description="Voir les rappels actifs",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolescan_status(self, interaction: discord.Interaction):
        with measure("slash:rolescan_status"):
            g = interaction.guild
            g_key = str(g.id)
            g_map = self.reminders.get(g_key, {})
            if not g_map:
                await interaction.response.send_message(
                    "Aucun rappel actif.",
                    ephemeral=True,
                )
                return

            now = _now_tz()
            lines = []
            for u_key, rec in g_map.items():
                try:
                    created_at = datetime.fromisoformat(
                        rec.get("created_at")
                    )
                except Exception:
                    created_at = now
                age_h = (now - created_at).total_seconds() / 3600
                member = g.get_member(int(u_key))
                name = member.mention if member else f"`{u_key}`"
                lines.append(
                    f"- {name} • âge: {age_h:.1f}h • msg: "
                    f"`{rec.get('message_id')}`"
                )

            out = "\n".join(lines[:50])
            if len(lines) > 50:
                out += f"\n… (+{len(lines)-50} autres)"
            await interaction.response.send_message(
                out or "Aucun rappel actif.",
                ephemeral=True,
            )

    @group.command(
        name="reset_user",
        description=(
            "Purger l’état d’un utilisateur "
            "(supprime son message si présent)"
        ),
    )
    @app_commands.describe(user="Utilisateur à réinitialiser")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rolescan_reset_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ):
        with measure("slash:rolescan_reset_user"):
            await interaction.response.defer(ephemeral=True, thinking=True)

            g = interaction.guild
            g_key = str(g.id)
            u_key = str(user.id)
            rec = self.reminders.get(g_key, {}).get(u_key)
            if not rec:
                await interaction.followup.send(
                    "Aucun état enregistré pour cet utilisateur.",
                    ephemeral=True,
                )
                return

            ch = g.get_channel(REMINDER_CHANNEL_ID)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(int(rec.get("message_id")))
                    await msg.delete()
                except Exception as e:
                    logging.debug("Failed to delete reminder message: %s", e)

            self.reminders.get(g_key, {}).pop(u_key, None)
            self._save_state()
            await interaction.followup.send("État purgé.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleReminderCog(bot))
    # enregistrer le groupe (idempotent)
    try:
        bot.tree.add_command(RoleReminderCog.group)
    except Exception as e:
        logging.debug("Failed to add RoleReminder group: %s", e)
