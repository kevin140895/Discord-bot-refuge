from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from models.refuge_world import RefugePanelState
from services.refuge_panel import (
    RefugePanelService,
    RefugePanelSnapshot,
    refuge_panel_service,
)
from services.refuge_world_coordination import refuge_world_mutation_lock
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from ui.refuge_panel_view import (
    REFUGE_MAP_FILENAME,
    RefugeLiveStatus,
    RefugePublicControlsView,
    RefugePublicPanelView,
    refuge_activity_presentation,
)
from utils.discord_utils import safe_message_edit
from utils.timezones import PARIS_TZ


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


DEFAULT_REFUGE_PANEL_CHANNEL_ID = 1536027732071161987
REFUGE_PANEL_CHANNEL_ID = max(
    0,
    _env_int("REFUGE_PANEL_CHANNEL_ID", DEFAULT_REFUGE_PANEL_CHANNEL_ID),
)
REFUGE_PANEL_REFRESH_SECONDS = max(
    30,
    _env_int("REFUGE_PANEL_REFRESH_SECONDS", 60),
)


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def refuge_day_number(
    created_at: str | None,
    *,
    at: datetime | None = None,
) -> int | None:
    """Return the persistent Refuge day number using Paris calendar days."""

    if not created_at:
        return None
    try:
        started = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    started_day = started.astimezone(PARIS_TZ).date()
    current_day = _aware_utc(at).astimezone(PARIS_TZ).date()
    return max(1, (current_day - started_day).days + 1)


def refuge_ambience(*, at: datetime | None = None) -> str:
    """Return a deterministic atmosphere sentence for the Paris daypart."""

    hour = _aware_utc(at).astimezone(PARIS_TZ).hour
    if 5 <= hour < 12:
        return "Le Refuge s’éveille doucement."
    if 12 <= hour < 18:
        return "L’activité bat son plein dans le Refuge."
    if 18 <= hour < 23:
        return "Les habitants se retrouvent autour du feu."
    return "Le Refuge s’endort, mais quelques lumières restent allumées."


def refuge_member_count(guild: discord.Guild) -> int:
    """Count human members using the same semantics as the stats cog."""

    members = tuple(getattr(guild, "members", ()) or ())
    bot_count = sum(1 for member in members if getattr(member, "bot", False))
    reported_count = getattr(guild, "member_count", None)
    if reported_count is None:
        return sum(1 for member in members if not getattr(member, "bot", False))
    try:
        total = int(reported_count)
    except (TypeError, ValueError):
        total = len(members)
    return max(0, total - bot_count)


def refuge_voice_count(guild: discord.Guild) -> int:
    """Count humans currently connected to any guild voice channel."""

    return sum(
        1
        for channel in (getattr(guild, "voice_channels", ()) or ())
        for member in (getattr(channel, "members", ()) or ())
        if not getattr(member, "bot", False)
    )


def refuge_radio_status(bot: commands.Bot) -> str:
    """Describe the existing RadioCog state without adding Discord API calls."""

    radio = bot.get_cog("RadioCog")
    if radio is None:
        return "Hors ligne"
    if getattr(radio, "stream_url", None) is None:
        # Music2 intentionally suspends the radio by clearing stream_url.
        return "En pause"

    voice = getattr(radio, "voice", None)
    is_connected = getattr(voice, "is_connected", None)
    if callable(is_connected) and bool(is_connected()):
        return "En ligne"
    return "Reconnexion"


def build_refuge_live_status(
    bot: commands.Bot,
    guild: discord.Guild,
    snapshot: RefugePanelSnapshot,
    *,
    at: datetime | None = None,
) -> RefugeLiveStatus:
    """Build the small live Discord layer shown above the persistent world state."""

    return RefugeLiveStatus(
        day_number=refuge_day_number(snapshot.state.created_at, at=at),
        member_count=refuge_member_count(guild),
        voice_count=refuge_voice_count(guild),
        radio_status=refuge_radio_status(bot),
        ambience=refuge_ambience(at=at),
    )


def refuge_live_visual_signature(world_signature: str, voice_count: int) -> str:
    """Combine persistent/hourly visuals with the coarse live activity bucket."""

    activity = refuge_activity_presentation(voice_count)
    return f"{world_signature}|activity:{activity.key}"


def panel_refresh_action(
    *,
    message_exists: bool,
    previous_visual_signature: str | None,
    previous_summary_signature: str | None,
    visual_signature: str,
    summary_signature: str,
) -> str:
    """Return create/render/summary/none without performing Discord I/O."""

    if not message_exists:
        return "create"
    if previous_visual_signature is None or previous_summary_signature is None:
        return "render"
    if previous_visual_signature != visual_signature:
        return "render"
    if previous_summary_signature != summary_signature:
        return "summary"
    return "none"


def panel_reference_needs_retirement(
    panel: RefugePanelState,
    *,
    target_channel_id: int,
) -> bool:
    """Whether a stored Refuge panel points at another configured channel."""

    return (
        panel.channel_id is not None
        and panel.message_id is not None
        and panel.channel_id != int(target_channel_id)
    )


class RefugePanelCog(commands.Cog):
    """Maintain exactly one low-churn public panel for the living Refuge."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        panel_service: RefugePanelService = refuge_panel_service,
        world_store: RefugeWorldStore = refuge_world_store,
    ) -> None:
        self.bot = bot
        self.panel_service = panel_service
        self.world_store = world_store
        self._refresh_lock = asyncio.Lock()
        self._last_visual_signature: str | None = None
        self._last_summary_signature: str | None = None

    async def cog_load(self) -> None:
        if not getattr(self.bot, "_refuge_public_controls_added", False):
            self.bot.add_view(RefugePublicControlsView())
            self.bot._refuge_public_controls_added = True  # type: ignore[attr-defined]

        if REFUGE_PANEL_CHANNEL_ID <= 0:
            logger.info(
                "[refuge] Panneau public désactivé: REFUGE_PANEL_CHANNEL_ID=0"
            )
            return
        self.refresh_panel.change_interval(seconds=REFUGE_PANEL_REFRESH_SECONDS)
        if not self.refresh_panel.is_running():
            self.refresh_panel.start()

    def cog_unload(self) -> None:
        self.refresh_panel.cancel()

    async def _resolve_channel(self) -> discord.TextChannel | discord.Thread | None:
        channel = self.bot.get_channel(REFUGE_PANEL_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(REFUGE_PANEL_CHANNEL_ID)
            except discord.HTTPException:
                logger.warning(
                    "[refuge] Salon du panneau introuvable: %s",
                    REFUGE_PANEL_CHANNEL_ID,
                )
                return None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "[refuge] REFUGE_PANEL_CHANNEL_ID=%s n'est pas un salon texte/thread",
                REFUGE_PANEL_CHANNEL_ID,
            )
            return None
        return channel

    async def _retire_previous_panel_if_moved(
        self,
        *,
        target_channel_id: int,
    ) -> bool:
        """Retire the previous panel before allowing a panel in a new channel.

        ``True`` means the old panel is definitively gone (or never existed).
        A transient Discord failure returns ``False`` so the caller preserves
        the stored reference and retries later instead of creating a duplicate.
        """

        state = await self.world_store.get_state()
        panel = state.panel
        if not panel_reference_needs_retirement(
            panel,
            target_channel_id=target_channel_id,
        ):
            return True

        assert panel.channel_id is not None
        assert panel.message_id is not None
        old_channel = self.bot.get_channel(panel.channel_id)
        if old_channel is None:
            try:
                old_channel = await self.bot.fetch_channel(panel.channel_id)
            except discord.NotFound:
                # The whole old channel is gone, so its panel is definitively gone.
                return True
            except discord.HTTPException:
                logger.warning(
                    "[refuge] Ancien salon du panneau temporairement inaccessible: %s",
                    panel.channel_id,
                )
                return False
        if not isinstance(old_channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "[refuge] Ancienne référence panneau non résolue vers un salon texte: %s",
                panel.channel_id,
            )
            return False
        try:
            old_message = await old_channel.fetch_message(panel.message_id)
            await old_message.delete()
        except discord.NotFound:
            return True
        except discord.HTTPException:
            logger.warning(
                "[refuge] Impossible de retirer temporairement l'ancien panneau %s/%s",
                panel.channel_id,
                panel.message_id,
            )
            return False
        return True

    async def _fetch_stored_message(
        self,
        channel: discord.TextChannel | discord.Thread,
    ) -> tuple[discord.Message | None, bool]:
        """Return ``(message, definitive)`` for the current stored panel.

        ``definitive=False`` means Discord could not confirm whether the
        message exists. The caller must not create a replacement in that case.
        """

        state = await self.world_store.get_state()
        panel = state.panel
        if panel.channel_id != channel.id or panel.message_id is None:
            return None, True
        try:
            return await channel.fetch_message(panel.message_id), True
        except discord.NotFound:
            return None, True
        except discord.HTTPException:
            logger.warning(
                "[refuge] État du panneau %s/%s temporairement inconnu; "
                "aucun duplicata ne sera créé",
                channel.id,
                panel.message_id,
            )
            return None, False

    async def _persist_panel_reference(self, message: discord.Message) -> None:
        desired = RefugePanelState(
            channel_id=message.channel.id,
            message_id=message.id,
        )
        # Panel ownership is part of RefugeWorldState. The full read-modify-
        # write must share the same mutation lock as Timeline, Construction and
        # Secrets, otherwise a concurrent world mutation can be overwritten by
        # a stale panel-only save.
        async with refuge_world_mutation_lock():
            state = await self.world_store.get_state()
            if state.panel == desired:
                return
            await self.world_store.save_state(replace(state, panel=desired))

    async def _render_file(
        self,
        snapshot: RefugePanelSnapshot,
        live_status: RefugeLiveStatus,
    ) -> discord.File:
        activity = refuge_activity_presentation(live_status.voice_count)
        png = await self.panel_service.render_png(
            snapshot,
            activity_key=activity.key,
        )
        return discord.File(
            io.BytesIO(png),
            filename=REFUGE_MAP_FILENAME,
        )

    async def ensure_panel(self) -> None:
        if REFUGE_PANEL_CHANNEL_ID <= 0:
            return
        async with self._refresh_lock:
            channel = await self._resolve_channel()
            if channel is None:
                return

            retired = await self._retire_previous_panel_if_moved(
                target_channel_id=channel.id,
            )
            if not retired:
                return

            message, lookup_definitive = await self._fetch_stored_message(channel)
            if not lookup_definitive:
                return

            snapshot = await self.panel_service.evaluate()
            live_status = build_refuge_live_status(
                self.bot,
                channel.guild,
                snapshot,
            )
            combined_visual_signature = refuge_live_visual_signature(
                snapshot.visual_signature,
                live_status.voice_count,
            )
            combined_summary_signature = (
                f"{snapshot.summary_signature}|live:{live_status.signature}"
            )
            action = panel_refresh_action(
                message_exists=message is not None,
                previous_visual_signature=self._last_visual_signature,
                previous_summary_signature=self._last_summary_signature,
                visual_signature=combined_visual_signature,
                summary_signature=combined_summary_signature,
            )

            if action == "none":
                return

            view = RefugePublicPanelView(snapshot, live_status=live_status)
            if action == "create":
                file = await self._render_file(snapshot, live_status)
                try:
                    message = await channel.send(file=file, view=view)
                except discord.HTTPException:
                    logger.exception("[refuge] Échec création du panneau public")
                    return
                await self._persist_panel_reference(message)
            elif action == "render":
                assert message is not None
                file = await self._render_file(snapshot, live_status)
                try:
                    edited = await safe_message_edit(
                        message,
                        attachments=[file],
                        view=view,
                    )
                except discord.HTTPException:
                    logger.exception("[refuge] Échec mise à jour graphique du panneau")
                    return
                if edited is not None:
                    message = edited
            else:
                assert action == "summary"
                assert message is not None
                try:
                    edited = await safe_message_edit(message, view=view)
                except discord.HTTPException:
                    logger.exception("[refuge] Échec mise à jour du résumé du panneau")
                    return
                if edited is not None:
                    message = edited

            self._last_visual_signature = combined_visual_signature
            self._last_summary_signature = combined_summary_signature
            if message is not None:
                await self._persist_panel_reference(message)

    @tasks.loop(seconds=60)
    async def refresh_panel(self) -> None:
        try:
            await self.ensure_panel()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[refuge] Rafraîchissement du panneau public échoué")

    @refresh_panel.before_loop
    async def before_refresh_panel(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugePanelCog(bot))


__all__ = [
    "DEFAULT_REFUGE_PANEL_CHANNEL_ID",
    "REFUGE_PANEL_CHANNEL_ID",
    "REFUGE_PANEL_REFRESH_SECONDS",
    "RefugePanelCog",
    "build_refuge_live_status",
    "panel_reference_needs_retirement",
    "panel_refresh_action",
    "refuge_ambience",
    "refuge_day_number",
    "refuge_live_visual_signature",
    "refuge_member_count",
    "refuge_radio_status",
    "refuge_voice_count",
]
