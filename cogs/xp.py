"""Système d'XP du serveur : messages, voix et statistiques quotidiennes.

La cog enregistre l'activité des membres, calcule l'XP et gère les
statistiques journalières. L'XP, les checkpoints vocaux actifs, les
statistiques quotidiennes et les boosts personnels sont persistés dans SQLite.
Les anciens JSON restent uniquement des sources de migration legacy.
"""

import asyncio
import io
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    ANNOUNCE_CHANNEL_ID,
    XP_VIEWER_ROLE_ID,
)
from utils.timezones import PARIS_TZ
from utils.interactions import safe_respond
from utils.persistence import (
    ensure_dir,
    schedule_checkpoint,
)
from utils.metrics import measure
from storage.db import database
from storage.xp_store import xp_store
from storage.season_store import season_store
from utils.game_events import get_multiplier, record_participant
from utils.voice_bonus import get_voice_bonus_windows
from utils.seasons import should_count_xp_source
from utils.refuge_casino_observer import observe_casino_xp_transaction
logger = logging.getLogger(__name__)

# Fichiers conservés uniquement comme sources de migration legacy ; toutes les
# nouvelles écritures de ces états vont dans ``refuge.db``.
VOICE_TIMES_FILE = os.path.join(DATA_DIR, "voice_times.json")
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")
XP_BOOSTS_FILE = os.path.join(DATA_DIR, "xp_boosts.json")

# S'assurer que le répertoire de données existe
ensure_dir(DATA_DIR)

# Caches en mémoire
voice_times: dict[str, datetime] = {}
XP_CACHE: dict[str, dict] = xp_store.data
DAILY_STATS: dict[str, dict[str, dict[str, int]]] = {}
XP_LOCK = xp_store.lock
DAILY_LOCK = asyncio.Lock()
# ``XP_BOOSTS`` reste un mapping vers la date d'expiration pour conserver le
# contrat utilisé par la boutique. Les débuts et anciens créneaux sont stockés
# séparément afin de calculer précisément les sessions vocales différées.
XP_BOOSTS: dict[str, datetime] = {}
XP_BOOST_STARTS: dict[str, datetime] = {}
XP_BOOST_HISTORY: dict[str, list[tuple[datetime, datetime]]] = {}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _prune_stale_daily_stats(current_day: str) -> None:
    """Remove cached daily statistics for days other than ``current_day``."""

    stale_days = [day for day in list(DAILY_STATS.keys()) if day != current_day]
    for day in stale_days:
        DAILY_STATS.pop(day, None)


async def load_voice_times() -> dict[str, datetime]:
    """Load active voice checkpoints from SQLite after one-time JSON import."""
    await database.migrate_legacy_voice_times(VOICE_TIMES_FILE)
    data = await database.load_voice_times()
    out: dict[str, datetime] = {}
    for uid, iso in data.items():
        try:
            out[uid] = datetime.fromisoformat(iso)
        except ValueError as e:
            logger.warning("Invalid voice time for user %s: %s", uid, e)
            continue
    return out


async def save_voice_times_to_disk() -> None:
    """Sauvegarde atomiquement les checkpoints vocaux actifs dans SQLite."""
    try:
        serializable = {
            uid: dt.astimezone(timezone.utc).isoformat()
            for uid, dt in voice_times.items()
        }
        await database.replace_voice_times(serializable)
        logger.info("[xp] Voice times sauvegardés dans SQLite")
    except sqlite3.Error as e:
        logger.exception("[xp] Échec sauvegarde SQLite voice times: %s", e)


async def load_daily_stats() -> dict:
    """Load daily statistics from SQLite after one-time JSON import."""
    await database.migrate_legacy_daily_stats(DAILY_STATS_FILE)
    return await database.load_daily_stats()


async def save_daily_stats_to_disk() -> None:
    """Persist one immutable snapshot of the in-memory daily statistics."""
    async with DAILY_LOCK:
        data = {
            day: {
                uid: dict(payload)
                for uid, payload in users.items()
            }
            for day, users in DAILY_STATS.items()
        }
    try:
        await database.replace_daily_stats(data)
        logger.info("[xp] Daily stats sauvegardées dans SQLite")
    except (sqlite3.Error, ValueError) as e:
        logger.exception("[xp] Échec sauvegarde SQLite daily stats: %s", e)


async def load_xp_boosts() -> tuple[
    dict[str, datetime],
    dict[str, datetime],
    dict[str, list[tuple[datetime, datetime]]],
]:
    """Load personal Double XP state after one-time legacy JSON import."""
    await database.migrate_legacy_xp_boosts(XP_BOOSTS_FILE)
    data = await database.load_xp_boosts()
    expiries: dict[str, datetime] = {}
    starts: dict[str, datetime] = {}
    history: dict[str, list[tuple[datetime, datetime]]] = {}
    now = datetime.now(timezone.utc)

    for uid, raw in data.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("unsupported boost record")

            expiry = _as_utc(datetime.fromisoformat(str(raw["expires_at"])))
            start_raw = raw.get("started_at")
            start = (
                _as_utc(datetime.fromisoformat(str(start_raw)))
                if start_raw
                else min(now, expiry)
            )
            expiries[uid] = expiry
            starts[uid] = start

            windows: list[tuple[datetime, datetime]] = []
            for item in raw.get("history", []):
                if not isinstance(item, dict):
                    continue
                window_start = _as_utc(datetime.fromisoformat(item["start"]))
                window_end = _as_utc(datetime.fromisoformat(item["end"]))
                if window_end > window_start:
                    windows.append((window_start, window_end))
            if windows:
                history[uid] = windows[-64:]
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Invalid XP boost for user %s: %s", uid, e)
            continue

    return expiries, starts, history


async def save_xp_boosts_to_disk() -> None:
    try:
        serializable: dict[str, dict] = {}
        for uid, expiry in XP_BOOSTS.items():
            start = XP_BOOST_STARTS.get(uid)
            serializable[uid] = {
                "started_at": (
                    _as_utc(start).isoformat() if start is not None else None
                ),
                "expires_at": _as_utc(expiry).isoformat(),
                "history": [
                    {
                        "start": _as_utc(window_start).isoformat(),
                        "end": _as_utc(window_end).isoformat(),
                    }
                    for window_start, window_end in XP_BOOST_HISTORY.get(uid, [])[-64:]
                ],
            }
        await database.replace_xp_boosts(serializable)
        logger.info("[xp] XP boosts sauvegardés dans SQLite")
    except (sqlite3.Error, ValueError) as e:
        logger.exception("[xp] Échec sauvegarde SQLite XP boosts: %s", e)


async def xp_bootstrap_cache() -> None:
    global XP_CACHE, voice_times, DAILY_STATS, XP_LOCK
    global XP_BOOSTS, XP_BOOST_STARTS, XP_BOOST_HISTORY
    XP_CACHE = xp_store.data
    XP_LOCK = xp_store.lock
    voice_times = await load_voice_times()
    DAILY_STATS = await load_daily_stats()
    today = datetime.now(PARIS_TZ).date().isoformat()
    _prune_stale_daily_stats(today)
    XP_BOOSTS, XP_BOOST_STARTS, XP_BOOST_HISTORY = await load_xp_boosts()
    logger.info("🎒 XP cache chargé (%d utilisateurs).", len(XP_CACHE))


async def xp_flush_cache_to_disk() -> None:
    await xp_store.flush()
    logger.info("💾 XP flush vers disque (%d utilisateurs).", len(xp_store.data))


async def award_xp(
    user_id: int,
    amount: int,
    guild_id: int | None = None,
    source: str = "manual",
    *,
    apply_personal_boost: bool = True,
) -> tuple[int, int, int, int]:
    """Modifie l'XP de ``user_id`` via le :class:`XPStore`.

    Les gains instantanés utilisent le Double XP personnel actif au moment de
    l'attribution. Les gains vocaux calculent eux-mêmes les intersections
    temporelles et passent ``apply_personal_boost=False`` pour éviter de doubler
    rétroactivement toute la session au moment de la déconnexion.
    """
    now = datetime.now(timezone.utc)
    requested_amount = int(amount)
    if amount > 0 and apply_personal_boost:
        boost_exp = XP_BOOSTS.get(str(user_id))
        if boost_exp and _as_utc(boost_exp) > now:
            amount *= 2
    result = await xp_store.add_xp(
        user_id, amount, guild_id=guild_id, source=source
    )
    old_level, new_level, old_xp, new_xp = result
    delta = new_xp - old_xp
    observe_casino_xp_transaction(
        user_id=user_id,
        source=source,
        requested_amount=requested_amount,
        applied_delta=delta,
        at=now,
    )
    if should_count_xp_source(source, delta):
        try:
            await season_store.record(user_id, at=now, xp_earned=delta)
        except Exception:
            logger.exception(
                "[season] Impossible d'enregistrer %s XP saisonnière pour %s",
                delta,
                user_id,
            )
    return result


def _remember_finished_personal_boost(
    uid: str, start: datetime | None, end: datetime | None
) -> None:
    if start is None or end is None:
        return
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc <= start_utc:
        return
    windows = XP_BOOST_HISTORY.setdefault(uid, [])
    window = (start_utc, end_utc)
    if window not in windows:
        windows.append(window)
        del windows[:-64]


def add_xp_boost(user_id: int, duration_minutes: float) -> None:
    """Active un bonus Double XP personnel pendant ``duration_minutes``."""
    uid = str(user_id)
    now = datetime.now(timezone.utc)
    current_expiry = XP_BOOSTS.get(uid)
    current_start = XP_BOOST_STARTS.get(uid)

    if current_expiry is not None and _as_utc(current_expiry) > now:
        # Une prolongation boutique doit conserver le début réel du boost.
        start = _as_utc(current_start) if current_start is not None else now
    else:
        _remember_finished_personal_boost(uid, current_start, current_expiry)
        start = now

    XP_BOOST_STARTS[uid] = start
    XP_BOOSTS[uid] = now + timedelta(minutes=float(duration_minutes))
    asyncio.create_task(save_xp_boosts_to_disk())


def _personal_boost_windows(
    user_id: int, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    uid = str(user_id)
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    candidates = list(XP_BOOST_HISTORY.get(uid, []))

    current_start = XP_BOOST_STARTS.get(uid)
    current_end = XP_BOOSTS.get(uid)
    if current_start is not None and current_end is not None:
        candidates.append((_as_utc(current_start), _as_utc(current_end)))

    matches: list[tuple[datetime, datetime]] = []
    for window_start, window_end in candidates:
        overlap_start = max(start_utc, window_start)
        overlap_end = min(end_utc, window_end)
        if overlap_end > overlap_start:
            matches.append((overlap_start, overlap_end))
    return matches


def _window_contains(
    moment: datetime, windows: list[tuple[datetime, datetime]]
) -> bool:
    return any(start <= moment < end for start, end in windows)


def calculate_voice_xp(
    user_id: int,
    start: datetime,
    end: datetime,
    event_multiplier: float = 1.0,
) -> int:
    """Calculate voice XP using only boost time that overlaps the session.

    The base system awards 3 XP per *completed* minute. We keep that rounding
    rule by limiting the billable interval to the completed minutes first, then
    split that interval on every global/personal boost boundary.
    """
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    completed_minutes = int((end_utc - start_utc).total_seconds() // 60)
    if completed_minutes <= 0:
        return 0

    billable_end = start_utc + timedelta(minutes=completed_minutes)
    global_windows = get_voice_bonus_windows(start_utc, billable_end)
    personal_windows = _personal_boost_windows(user_id, start_utc, billable_end)

    boundaries = {start_utc, billable_end}
    for window_start, window_end in global_windows + personal_windows:
        boundaries.add(max(start_utc, window_start))
        boundaries.add(min(billable_end, window_end))
    ordered = sorted(boundaries)

    total_xp = 0.0
    base_event_multiplier = float(event_multiplier)
    for segment_start, segment_end in zip(ordered, ordered[1:], strict=False):
        if segment_end <= segment_start:
            continue
        midpoint = segment_start + (segment_end - segment_start) / 2
        multiplier = base_event_multiplier
        if _window_contains(midpoint, global_windows) and multiplier < 2.0:
            multiplier = 2.0
        if _window_contains(midpoint, personal_windows):
            multiplier *= 2.0
        minutes = (segment_end - segment_start).total_seconds() / 60.0
        total_xp += minutes * 3.0 * multiplier

    return int(total_xp + 1e-9)


async def generate_rank_card(user: discord.User, level: int, xp: int, xp_needed: int):
    def _draw() -> io.BytesIO:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (460, 140), color=(30, 41, 59))
        draw = ImageDraw.Draw(img)
        draw.text((16, 14), f"{user.name} — Niveau {level}", fill=(255, 255, 255))
        draw.text((16, 52), f"XP: {xp} / {xp_needed}", fill=(220, 220, 220))
        bar_x, bar_y, bar_w, bar_h = 16, 90, 428, 22
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(71, 85, 105))
        ratio = max(0.0, min(1.0, xp / max(1, xp_needed)))
        draw.rectangle([bar_x, bar_y, bar_x + int(bar_w * ratio), bar_y + bar_h], fill=(34, 197, 94))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    return await asyncio.to_thread(_draw)


class XPCog(commands.Cog):
    """Fonctionnalités liées à l'XP."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_backup_xp.start()
        self._message_cooldown = commands.CooldownMapping.from_cooldown(
            1, 60.0, commands.BucketType.user
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        now = datetime.now(timezone.utc)
        active: set[str] = set()
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    uid = str(member.id)
                    active.add(uid)
                    # A redémarrage, l'ancien timestamp n'est plus une borne fiable :
                    # le bot ne peut pas observer le temps passé hors ligne.
                    voice_times[uid] = now
        for uid in list(voice_times.keys()):
            if uid not in active:
                voice_times.pop(uid, None)
        await schedule_checkpoint(save_voice_times_to_disk)

    def cog_unload(self) -> None:
        self.auto_backup_xp.cancel()

    @tasks.loop(minutes=10)
    async def auto_backup_xp(self) -> None:
        await xp_flush_cache_to_disk()
        try:
            await save_voice_times_to_disk()
        except (OSError, sqlite3.Error) as e:
            logger.exception("[xp] auto_backup_xp: exception: %s", e)
        await save_daily_stats_to_disk()
        await save_xp_boosts_to_disk()
        logger.info("🛟 Sauvegarde périodique effectuée.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        # Statistiques quotidiennes
        today = datetime.now(PARIS_TZ).date().isoformat()
        async with DAILY_LOCK:
            _prune_stale_daily_stats(today)
            day = DAILY_STATS.setdefault(today, {})
            user = day.setdefault(str(message.author.id), {"messages": 0, "voice": 0})
            user["messages"] = int(user.get("messages", 0)) + 1
        await schedule_checkpoint(save_daily_stats_to_disk)

        bucket = self._message_cooldown.get_bucket(message)
        if bucket.update_rate_limit():
            return
        amount = 8
        old_lvl, new_lvl, old_xp, new_xp = await award_xp(
            message.author.id,
            amount,
            guild_id=message.guild.id,
            source="message",
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            uid = str(member.id)
            voice_times.pop(uid, None)
            await schedule_checkpoint(save_voice_times_to_disk)
            return
        # Ignorer si l'utilisateur ne change pas réellement de salon
        if before.channel == after.channel:
            await schedule_checkpoint(save_voice_times_to_disk)
            return

        now = datetime.now(PARIS_TZ)
        uid = str(member.id)

        # Déconnexion ou changement de salon : calculer la durée et attribuer l'XP
        if before.channel is not None:
            start = voice_times.pop(uid, None)
            if start is not None:
                duration = now - start
                event_mult = get_multiplier(before.channel.id, member.id)
                if event_mult != 1.0:
                    record_participant(before.channel.id, member.id)
                xp_amount = calculate_voice_xp(
                    member.id,
                    start,
                    now,
                    event_multiplier=event_mult,
                )
                old_lvl, new_lvl, old_xp, new_xp = await award_xp(
                    member.id,
                    xp_amount,
                    guild_id=member.guild.id,
                    source="voice_leave",
                    apply_personal_boost=False,
                )
                # Statistiques quotidiennes (en secondes)
                day = now.date().isoformat()
                async with DAILY_LOCK:
                    _prune_stale_daily_stats(day)
                    d = DAILY_STATS.setdefault(day, {})
                    u = d.setdefault(uid, {"messages": 0, "voice": 0})
                    u["voice"] = int(u.get("voice", 0)) + int(duration.total_seconds())
                    should_thank = (
                        after.channel is None
                        and u["voice"] >= 2 * 3600
                        and not u.get("voice_thanked")
                    )
                    if should_thank:
                        u["voice_thanked"] = True
                await schedule_checkpoint(save_daily_stats_to_disk)
                if should_thank:
                    channel = member.guild.get_channel(ANNOUNCE_CHANNEL_ID)
                    if channel is not None:
                        await channel.send(
                            (
                                f"🎧✨ Merci à toi {member.mention} !\n"
                                "Tu viens de passer plus de 2h en vocal dans Le Refuge 🕑\n"
                                "Ta présence fait vivre la communauté et rend nos moments encore plus agréables 🙌\n\n"
                                "Continue à partager ces instants avec nous"
                            )
                        )

        # Connexion à un nouveau salon
        if after.channel is not None:
            voice_times[uid] = now

        await schedule_checkpoint(save_voice_times_to_disk)

    @auto_backup_xp.before_loop
    async def before_auto_backup_xp(self) -> None:
        await self.bot.wait_until_ready()


    @app_commands.command(name="don_xp", description="Donne de l'XP à un membre")
    @app_commands.checks.has_role(XP_VIEWER_ROLE_ID)
    @app_commands.describe(
        membre="Membre qui reçoit l'XP",
        montant="Quantité d'XP à ajouter",
    )
    async def don_xp(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
        montant: app_commands.Range[int, 1],
    ) -> None:
        with measure("slash:don_xp"):
            old_lvl, new_lvl, old_xp, new_xp = await award_xp(
                membre.id,
                montant,
                guild_id=interaction.guild.id if interaction.guild else None,
                source="don_xp",
            )
            await safe_respond(
                interaction,
                f"{membre.display_name} reçoit {montant} XP. Total: {new_xp} XP (niveau {new_lvl}).",
                ephemeral=True,
            )


    @app_commands.command(name="rang", description="Affiche ton niveau avec une carte graphique")
    async def rang(self, interaction: discord.Interaction) -> None:
        with measure("slash:rang"):
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except discord.Forbidden:
                logger.warning("[xp] Permissions insuffisantes pour différer la réponse")
            except discord.NotFound:
                logger.warning("[xp] Interaction introuvable lors du defer")
            except discord.HTTPException as e:
                logger.error("[xp] Erreur HTTP lors du defer: %s", e)
            except Exception as e:
                logger.exception("[xp] Erreur inattendue lors du defer: %s", e)
            user_id = str(interaction.user.id)
            async with XP_LOCK:
                data = XP_CACHE.get(user_id)
                if not data:
                    await interaction.followup.send(
                        "Tu n'as pas encore de niveau... Commence à discuter !",
                        ephemeral=True,
                    )
                    return
                level = int(data.get("level", 0))
                xp = int(data.get("xp", 0))
                xp_next = (level + 1) ** 2 * 100
            try:
                image = await generate_rank_card(interaction.user, level, xp, xp_next)
                file = discord.File(fp=image, filename="rank.png")
                await interaction.followup.send(file=file, ephemeral=True)
            except discord.Forbidden:
                logger.warning(
                    "[xp] Permissions insuffisantes pour envoyer la carte de rang"
                )
                await interaction.followup.send(
                    "❌ Permissions insuffisantes.", ephemeral=True
                )
            except discord.NotFound:
                logger.warning(
                    "[xp] Interaction ou ressource introuvable lors de l'envoi de la carte"
                )
                await interaction.followup.send(
                    "❌ Ressource introuvable.", ephemeral=True
                )
            except discord.HTTPException as e:
                logger.error(f"/rang: erreur HTTP lors de l'envoi de la carte: {e}")
                await interaction.followup.send(
                    "❌ Erreur HTTP lors de la génération de la carte.",
                    ephemeral=True,
                )
            except Exception as e:
                logger.exception(f"/rang: exception inattendue: {e}")
                await interaction.followup.send(
                    "❌ Une erreur est survenue pendant la génération de la carte.",
                    ephemeral=True,
                )

    @app_commands.command(name="xp_serveur", description="Affiche l'XP de tous les membres du serveur")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_serveur(self, interaction: discord.Interaction) -> None:
        with measure("slash:xp_serveur"):
            async with XP_LOCK:
                items = list(XP_CACHE.items())
            if not items:
                await safe_respond(interaction, "Aucune donnée XP.", ephemeral=True)
                return
            lines = []
            for uid, data in sorted(items, key=lambda x: x[1].get("xp", 0), reverse=True):
                member = interaction.guild.get_member(int(uid)) if interaction.guild else None
                if not member or member.bot:
                    continue
                xp = int(data.get("xp", 0))
                lvl = int(data.get("level", 0))
                lines.append(f"{member.display_name} - {xp} XP (niveau {lvl})")
            if not lines:
                await safe_respond(interaction, "Aucun membre trouvé.", ephemeral=True)
                return
            report = '\n'.join(lines)
            if len(report) < 1900:
                await safe_respond(interaction, f"```\n{report}\n```", ephemeral=True)
            else:
                file = discord.File(io.StringIO(report), filename="xp_serveur.txt")
                await safe_respond(interaction, "📄 Liste XP en pièce jointe.", ephemeral=True, file=file)

async def setup(bot: commands.Bot) -> None:
    await xp_bootstrap_cache()
    await bot.add_cog(XPCog(bot))
