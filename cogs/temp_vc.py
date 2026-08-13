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
from storage.temp_vc_control_store import load_temp_vc_owners
from storage.temp_vc_store import (
    GENERIC_TEMP_VC_TYPE,
    TempVCRecord,
    build_temp_vc_record,
    load_temp_vc_ids,
    load_temp_vc_registry,
    load_last_names_cache,
    save_last_names_cache,
    save_temp_vc_registry_async,
)
from utils.temp_vc_cleanup import delete_empty_managed_temp_vcs
from utils.rename_manager import rename_manager

logger = logging.getLogger(__name__)

# Registre de provenance: seule source d'autorité pour les suppressions automatiques.
TEMP_VC_REGISTRY: Dict[int, TempVCRecord] = load_temp_vc_registry()
TEMP_VC_IDS: Set[int] = {
    channel_id
    for channel_id, record in TEMP_VC_REGISTRY.items()
    if record["type"] == GENERIC_TEMP_VC_TYPE
}

# Mapping « rôle principal → nom de base du salon »
ROLE_NAMES: Dict[int, str] = {
    ROLE_PC: "PC",
    ROLE_CONSOLE: "Console",
    ROLE_MOBILE: "Mobile",
}


class TempVCCog(commands.Cog):
    """Création et maintenance des salons vocaux temporaires génériques."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._rename_tasks: Dict[int, asyncio.Task] = {}
        self._last_names: Dict[int, str] = {}

        # Aucun salon n'est découvert/adopté depuis son nom ou sa catégorie.
        self.cleanup.start()
        self.monitor_rename_worker.start()
        self.health_check.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Démarrage garanti du rename_manager et chargement du cache."""
        await self._ensure_rename_worker()
        await self._migrate_legacy_temp_vcs()
        await self._load_last_names_cache()

    def cog_unload(self) -> None:
        self.cleanup.cancel()
        self.monitor_rename_worker.cancel()
        self.health_check.cancel()
        for task in self._rename_tasks.values():
            task.cancel()
        self._rename_tasks.clear()

    async def _ensure_rename_worker(self) -> bool:
        """Start the rename manager worker if it's not running.

        Returns True if the worker is active, False otherwise.
        """
        if rename_manager._worker is None or rename_manager._worker.done():
            try:
                await rename_manager.start()
            except Exception:
                logger.exception("[temp_vc] échec du démarrage du worker rename_manager")
                return False
            else:
                logger.info("[temp_vc] rename_manager worker démarré")
        return True

    async def _save_last_names_cache(self) -> None:
        """Persiste le cache des derniers noms."""
        try:
            await save_last_names_cache(self._last_names.copy())
        except Exception:
            logger.exception("[temp_vc] échec de sauvegarde du cache des noms")

    async def _load_last_names_cache(self) -> None:
        """Charge le cache des derniers noms au démarrage."""
        try:
            data = load_last_names_cache()
        except Exception:
            logger.exception("[temp_vc] échec de lecture du cache des noms")
        else:
            if data:
                self._last_names.update(data)

    async def _migrate_legacy_temp_vcs(self) -> None:
        """Migrate legacy IDs only when independent provenance can be reconstructed.

        An ID from the old file is never enough on its own. Migration requires an
        existing generic voice channel in the configured category plus a persisted
        owner from the controls store. ``created_at`` comes from the Discord
        snowflake-backed channel property. Names are deliberately ignored.
        """
        try:
            legacy_ids = load_temp_vc_ids()
            legacy_owners = load_temp_vc_owners()
        except Exception:
            logger.exception("[temp_vc] échec de lecture des stores legacy")
            return

        changed = False
        for channel_id in legacy_ids:
            if channel_id in TEMP_VC_REGISTRY:
                continue

            channel = self.bot.get_channel(channel_id)
            owner_id = legacy_owners.get(channel_id)
            if not isinstance(channel, discord.VoiceChannel) or owner_id is None:
                continue
            if channel.category_id != TEMP_VC_CATEGORY:
                continue

            try:
                record = build_temp_vc_record(
                    channel.id,
                    owner_id,
                    channel.created_at.isoformat(),
                    record_type=GENERIC_TEMP_VC_TYPE,
                )
            except (TypeError, ValueError):
                logger.warning(
                    "[temp_vc] migration legacy ignorée pour %s: provenance invalide",
                    channel_id,
                )
                continue

            TEMP_VC_REGISTRY[channel_id] = record
            TEMP_VC_IDS.add(channel_id)
            changed = True

        if changed:
            await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())
            logger.info(
                "[temp_vc] migration legacy sûre terminée: %d salon(s) géré(s)",
                len(TEMP_VC_IDS),
            )

    # ---------- outils internes ----------

    def _base_name_for(self, member: discord.Member) -> str:
        """Retourne le nom de base du salon selon le rôle principal."""
        for rid, name in ROLE_NAMES.items():
            if any(r.id == rid for r in member.roles):
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

    def _get_primary_activity(self, member: discord.Member) -> str | None:
        """Détecte l'activité principale d'un membre."""
        acts = list(member.activities)

        for act in acts:
            if isinstance(act, discord.Game) or (
                isinstance(act, discord.Activity)
                and act.type is discord.ActivityType.playing
            ):
                return act.name

        for act in acts:
            if isinstance(act, discord.Streaming) or (
                isinstance(act, discord.Activity)
                and act.type is discord.ActivityType.streaming
            ):
                return act.name

        for act in acts:
            if isinstance(act, discord.Spotify):
                return act.title
            if isinstance(act, discord.Activity) and act.type is discord.ActivityType.listening:
                return act.name

        for act in acts:
            if isinstance(act, discord.CustomActivity) or (
                isinstance(act, discord.Activity)
                and act.type is discord.ActivityType.custom
            ):
                if getattr(act, "name", None):
                    return act.name
                if getattr(act, "state", None):
                    return act.state

        return None

    def _compute_channel_name(self, channel: discord.VoiceChannel) -> str | None:
        """Calcule le nom attendu pour le salon selon les membres."""
        if not channel.members:
            return None

        base = self._base_name_from_members(channel.members)

        # PRIORITÉ : activité > "AFK" (si mute) > "Chat"
        activity_counts: Dict[str, int] = {}
        for m in channel.members:
            act_name = self._get_primary_activity(m)
            if act_name:
                activity_counts[act_name] = activity_counts.get(act_name, 0) + 1

        if activity_counts:
            activity_name = max(activity_counts, key=activity_counts.get)
            max_status_len = 100 - len(base) - 3  # " • "
            status = activity_name[:max_status_len]
        elif any(m.voice and m.voice.self_mute for m in channel.members):
            status = "AFK"
        else:
            status = "Chat"

        name = f"{base} • {status}"
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
                    await self._save_last_names_cache()

        except asyncio.CancelledError:
            pass
        finally:
            if self._rename_tasks.get(channel.id) is asyncio.current_task():
                self._rename_tasks.pop(channel.id, None)

    async def _update_channel_name(self, channel: discord.VoiceChannel) -> None:
        """Programme ou reprogramme le renommage du salon après un délai."""
        if not channel.guild or channel.guild.get_channel(channel.id) is None:
            return
        if channel.id not in TEMP_VC_IDS:
            return

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
        if channel.guild.get_channel(channel.id) is None:
            return

        new_task = asyncio.create_task(self._rename_channel(channel))
        self._rename_tasks[channel.id] = new_task

    def _resolve_user_limit(self, base: str) -> int | None:
        """Détermine la limite user_limit pour un salon (fallback propre)."""
        if isinstance(TEMP_VC_LIMITS, dict):
            if base in TEMP_VC_LIMITS:
                return TEMP_VC_LIMITS.get(base)
            if TEMP_VC_CATEGORY in TEMP_VC_LIMITS:
                return TEMP_VC_LIMITS.get(TEMP_VC_CATEGORY)
        return None

    async def _create_temp_vc(self, member: discord.Member) -> discord.VoiceChannel:
        """Crée un salon vocal temporaire et persiste sa preuve de provenance."""
        category = self.bot.get_channel(TEMP_VC_CATEGORY)
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("TEMP_VC_CATEGORY invalide")

        base = self._base_name_for(member)
        limit = self._resolve_user_limit(base)

        channel = await category.create_voice_channel(base, user_limit=limit)
        record = build_temp_vc_record(
            channel.id,
            member.id,
            channel.created_at.isoformat(),
            record_type=GENERIC_TEMP_VC_TYPE,
        )

        TEMP_VC_REGISTRY[channel.id] = record
        TEMP_VC_IDS.add(channel.id)
        self._last_names[channel.id] = channel.name
        try:
            await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())
        except Exception:
            TEMP_VC_REGISTRY.pop(channel.id, None)
            TEMP_VC_IDS.discard(channel.id)
            self._last_names.pop(channel.id, None)
            try:
                await channel.delete(reason="Échec enregistrement provenance Temp VC")
            except discord.HTTPException:
                logger.exception(
                    "[temp_vc] suppression du salon %s après échec du registre impossible",
                    channel.id,
                )
            raise

        await self._save_last_names_cache()
        return channel

    # ----------- événements Discord -----------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # 1) Création quand on rejoint le lobby générique
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
            except discord.HTTPException:
                logger.exception(
                    "[temp_vc] failed to move %s (%s) into temporary channel '%s' (ID %s)",
                    member,
                    member.id,
                    new_vc.name,
                    new_vc.id,
                )
                await new_vc.delete(reason="Échec du déplacement du membre")
                TEMP_VC_REGISTRY.pop(new_vc.id, None)
                TEMP_VC_IDS.discard(new_vc.id)
                self._last_names.pop(new_vc.id, None)
                await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())
                await self._save_last_names_cache()
                return

            await self._update_channel_name(new_vc)
            return

        # 2) Suppression du salon temporaire quand il se vide
        if before.channel and before.channel.id in TEMP_VC_IDS:
            if not before.channel.members:
                try:
                    await before.channel.delete(reason="Salon temporaire vide")
                except discord.HTTPException:
                    logger.exception("Suppression du salon %s échouée", before.channel.id)
                else:
                    task = self._rename_tasks.pop(before.channel.id, None)
                    if task:
                        task.cancel()

                    TEMP_VC_REGISTRY.pop(before.channel.id, None)
                    TEMP_VC_IDS.discard(before.channel.id)
                    self._last_names.pop(before.channel.id, None)
                    await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())
                    await self._save_last_names_cache()

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
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        """Renomme le salon quand un membre commence/arrête un jeu."""
        if after.voice and after.voice.channel and after.voice.channel.id in TEMP_VC_IDS:
            await self._update_channel_name(after.voice.channel)

    # ---------- surveillance ----------

    @tasks.loop(minutes=5)
    async def monitor_rename_worker(self) -> None:
        if rename_manager._worker is None or rename_manager._worker.done():
            logger.warning("[temp_vc] worker rename_manager inactif; redémarrage")
            await self._ensure_rename_worker()

    @monitor_rename_worker.before_loop
    async def before_monitor_rename_worker(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=2)
    async def health_check(self) -> None:
        try:
            if rename_manager._worker is None or rename_manager._worker.done():
                logger.warning("[temp_vc] worker inactif détecté par health_check")
                await self._ensure_rename_worker()

            removed = False
            for cid in list(TEMP_VC_IDS):
                if self.bot.get_channel(cid) is None:
                    TEMP_VC_REGISTRY.pop(cid, None)
                    TEMP_VC_IDS.discard(cid)
                    self._last_names.pop(cid, None)
                    removed = True
            if removed:
                await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())
                await self._save_last_names_cache()

            stale = False
            for cid in list(self._last_names):
                if cid not in TEMP_VC_IDS:
                    self._last_names.pop(cid, None)
                    stale = True
            if stale:
                await self._save_last_names_cache()

            for cid, task in list(self._rename_tasks.items()):
                if task.done():
                    self._rename_tasks.pop(cid, None)
        except Exception:
            logger.exception("[temp_vc] échec de health_check")

    @health_check.before_loop
    async def before_health_check(self) -> None:
        await self.bot.wait_until_ready()

    # ---------- tâche de nettoyage ----------

    @tasks.loop(seconds=TEMP_VC_CHECK_INTERVAL_SECONDS)
    async def cleanup(self) -> None:
        try:
            for channel_id in list(TEMP_VC_IDS):
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.VoiceChannel):
                    await self._update_channel_name(channel)

            deleted_ids = await delete_empty_managed_temp_vcs(
                self.bot,
                TEMP_VC_REGISTRY.copy(),
            )
            if deleted_ids:
                for channel_id in deleted_ids:
                    task = self._rename_tasks.pop(channel_id, None)
                    if task:
                        task.cancel()
                    TEMP_VC_REGISTRY.pop(channel_id, None)
                    TEMP_VC_IDS.discard(channel_id)
                    self._last_names.pop(channel_id, None)
                await save_temp_vc_registry_async(TEMP_VC_REGISTRY.copy())

            await self._save_last_names_cache()
        except Exception:
            logger.exception("Erreur dans cleanup")

    @cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVCCog(bot))
