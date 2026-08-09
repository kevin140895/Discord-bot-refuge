from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import replace

import discord
from discord.ext import commands, tasks

from models.refuge_world import RefugePanelState
from services.refuge_panel import RefugePanelService, refuge_panel_service
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from ui.refuge_panel_view import (
    REFUGE_MAP_FILENAME,
    RefugePublicControlsView,
    RefugePublicPanelView,
)
from utils.discord_utils import safe_message_edit


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


REFUGE_PANEL_CHANNEL_ID = max(0, _env_int("REFUGE_PANEL_CHANNEL_ID", 0))
REFUGE_PANEL_REFRESH_SECONDS = max(
    30,
    _env_int("REFUGE_PANEL_REFRESH_SECONDS", 60),
)


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

    async def _fetch_stored_message(
        self,
        channel: discord.TextChannel | discord.Thread,
    ) -> discord.Message | None:
        state = await self.world_store.get_state()
        panel = state.panel
        if panel.channel_id != channel.id or panel.message_id is None:
            return None
        try:
            return await channel.fetch_message(panel.message_id)
        except discord.NotFound:
            return None
        except discord.HTTPException:
            logger.warning(
                "[refuge] Impossible de récupérer le panneau %s/%s",
                channel.id,
                panel.message_id,
            )
            return None

    async def _persist_panel_reference(self, message: discord.Message) -> None:
        state = await self.world_store.get_state()
        desired = RefugePanelState(
            channel_id=message.channel.id,
            message_id=message.id,
        )
        if state.panel == desired:
            return
        await self.world_store.save_state(replace(state, panel=desired))

    async def _render_file(self, snapshot) -> discord.File:
        png = await self.panel_service.render_png(snapshot)
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

            message = await self._fetch_stored_message(channel)
            snapshot = await self.panel_service.evaluate()
            action = panel_refresh_action(
                message_exists=message is not None,
                previous_visual_signature=self._last_visual_signature,
                previous_summary_signature=self._last_summary_signature,
                visual_signature=snapshot.visual_signature,
                summary_signature=snapshot.summary_signature,
            )

            if action == "none":
                return

            view = RefugePublicPanelView(snapshot)
            if action == "create":
                file = await self._render_file(snapshot)
                try:
                    message = await channel.send(file=file, view=view)
                except discord.HTTPException:
                    logger.exception("[refuge] Échec création du panneau public")
                    return
                await self._persist_panel_reference(message)
            elif action == "render":
                assert message is not None
                file = await self._render_file(snapshot)
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

            self._last_visual_signature = snapshot.visual_signature
            self._last_summary_signature = snapshot.summary_signature
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
    "REFUGE_PANEL_CHANNEL_ID",
    "REFUGE_PANEL_REFRESH_SECONDS",
    "RefugePanelCog",
    "panel_refresh_action",
]
