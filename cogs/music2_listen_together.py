from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import commands, tasks

from config import LOBBY_TEXT_CHANNEL, RADIO_VC_ID


logger = logging.getLogger(__name__)


def _env_channel_id(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


LISTEN_TOGETHER_CHANNEL_ID = _env_channel_id(
    "MUSIC_LISTEN_TOGETHER_CHANNEL_ID",
    LOBBY_TEXT_CHANNEL,
)
LISTEN_TOGETHER_CUSTOM_ID = "music2_listen_together_join"
LISTEN_TOGETHER_TITLE = "🎧 Écoute ensemble"


@dataclass(frozen=True, slots=True)
class ListenTogetherState:
    title: str
    uploader: str | None
    requester_id: int
    queue_size: int
    listener_ids: tuple[int, ...]

    @property
    def listener_count(self) -> int:
        return len(self.listener_ids)


class ListenTogetherView(discord.ui.View):
    def __init__(self, cog: "Music2ListenTogetherCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Rejoindre",
        emoji="🔊",
        style=discord.ButtonStyle.success,
        custom_id=LISTEN_TOGETHER_CUSTOM_ID,
    )
    async def join(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.join_listening_session(interaction)


class Music2ListenTogetherCog(commands.Cog):
    """Temporary general-channel invitation while custom Music 2.0 is playing."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.view = ListenTogetherView(self)
        self._message: discord.Message | None = None
        self._last_state: ListenTogetherState | None = None
        self._initial_cleanup_done = False
        self._sync_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        if not self.sync_announcement.is_running():
            self.sync_announcement.start()

    def cog_unload(self) -> None:
        self.sync_announcement.cancel()

    def _music_cog(self) -> Any | None:
        return self.bot.get_cog("Music2Cog")

    def _radio_cog(self) -> Any | None:
        return self.bot.get_cog("RadioCog")

    def _voice_channel(self) -> Any | None:
        return self.bot.get_channel(RADIO_VC_ID)

    async def _announcement_channel(self) -> Any | None:
        channel = self.bot.get_channel(LISTEN_TOGETHER_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(LISTEN_TOGETHER_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "[listen-together] salon d'annonce introuvable: %s",
                    LISTEN_TOGETHER_CHANNEL_ID,
                )
                return None
        if not callable(getattr(channel, "send", None)):
            logger.warning(
                "[listen-together] salon d'annonce non compatible: %s",
                LISTEN_TOGETHER_CHANNEL_ID,
            )
            return None
        return channel

    @staticmethod
    def _voice_is_active(voice: Any | None) -> bool:
        if voice is None:
            return False
        try:
            return bool(voice.is_playing() or voice.is_paused())
        except (AttributeError, TypeError):
            return False

    def _current_state(self) -> ListenTogetherState | None:
        music = self._music_cog()
        radio = self._radio_cog()
        if music is None or radio is None:
            return None

        track = getattr(music, "current", None)
        if track is None:
            return None
        if getattr(radio, "stream_url", None):
            return None
        if not self._voice_is_active(getattr(radio, "voice", None)):
            return None

        voice_channel = self._voice_channel()
        members = tuple(getattr(voice_channel, "members", ()) or ())
        listener_ids = tuple(
            sorted(
                int(member.id)
                for member in members
                if not getattr(member, "bot", False)
                and getattr(member, "id", None) is not None
            )
        )
        queue = getattr(music, "queue", ())
        try:
            queue_size = len(queue)
        except TypeError:
            queue_size = 0

        return ListenTogetherState(
            title=str(getattr(track, "title", "Titre inconnu") or "Titre inconnu"),
            uploader=(
                str(getattr(track, "uploader", "") or "").strip() or None
            ),
            requester_id=int(getattr(track, "requester_id", 0) or 0),
            queue_size=max(0, int(queue_size)),
            listener_ids=listener_ids,
        )

    @staticmethod
    def _build_embed(state: ListenTogetherState) -> discord.Embed:
        description = f"🔴 **{state.title}**"
        if state.uploader:
            description += f"\n🎤 {state.uploader}"
        if state.requester_id:
            description += f"\n👤 Demandé par <@{state.requester_id}>"
        description += (
            f"\n👥 **{state.listener_count}** personne(s) à l'écoute"
            f"\n📃 **{state.queue_size}** titre(s) en attente"
            "\n\nClique sur **🔊 Rejoindre** pour rejoindre la session."
        )
        embed = discord.Embed(
            title=LISTEN_TOGETHER_TITLE,
            description=description,
            color=discord.Color.red(),
        )
        embed.set_footer(text="Refuge • Music 2.0")
        return embed

    @staticmethod
    def _is_listen_together_message(message: Any) -> bool:
        embeds = getattr(message, "embeds", ()) or ()
        if embeds and getattr(embeds[0], "title", None) == LISTEN_TOGETHER_TITLE:
            return True
        return any(
            getattr(component, "custom_id", "") == LISTEN_TOGETHER_CUSTOM_ID
            for row in getattr(message, "components", ()) or ()
            for component in getattr(row, "children", ()) or ()
        )

    async def _cleanup_stale_messages(self, channel: Any) -> None:
        if self._initial_cleanup_done:
            return
        self._initial_cleanup_done = True

        history = getattr(channel, "history", None)
        bot_user = getattr(self.bot, "user", None)
        bot_user_id = getattr(bot_user, "id", None)
        if not callable(history) or bot_user_id is None:
            return

        try:
            async for message in history(limit=50):
                author = getattr(message, "author", None)
                if getattr(author, "id", None) != bot_user_id:
                    continue
                if not self._is_listen_together_message(message):
                    continue
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.debug(
                        "[listen-together] ancienne annonce impossible à supprimer"
                    )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[listen-together] historique du salon inaccessible")

    async def _delete_announcement(self) -> None:
        message = self._message
        self._message = None
        self._last_state = None
        if message is None:
            return
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[listen-together] suppression de l'annonce impossible")

    async def _sync_announcement(self) -> None:
        async with self._sync_lock:
            channel = await self._announcement_channel()
            if channel is None:
                return

            await self._cleanup_stale_messages(channel)
            state = self._current_state()
            if state is None:
                await self._delete_announcement()
                return

            embed = self._build_embed(state)
            if self._message is None:
                try:
                    self._message = await channel.send(embed=embed, view=self.view)
                    self._last_state = state
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "[listen-together] création de l'annonce impossible"
                    )
                return

            if state == self._last_state:
                return

            try:
                await self._message.edit(embed=embed, view=self.view)
                self._last_state = state
            except discord.NotFound:
                self._message = None
                self._last_state = None
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "[listen-together] mise à jour de l'annonce impossible"
                )

    async def join_listening_session(self, interaction: discord.Interaction) -> None:
        if self._current_state() is None:
            await interaction.response.send_message(
                "ℹ️ Cette session d'écoute est terminée.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        member = interaction.user
        if guild is None or not callable(getattr(member, "move_to", None)):
            await interaction.response.send_message(
                "❌ Action disponible uniquement sur le serveur.",
                ephemeral=True,
            )
            return

        target = guild.get_channel(RADIO_VC_ID) or self._voice_channel()
        if target is None:
            await interaction.response.send_message(
                "❌ Le salon musique est introuvable.",
                ephemeral=True,
            )
            return

        voice_state = getattr(member, "voice", None)
        current_channel = getattr(voice_state, "channel", None)
        if current_channel is None:
            await interaction.response.send_message(
                (
                    "🔊 Connecte-toi d'abord à un salon vocal, puis reclique sur "
                    f"**Rejoindre**. La musique est dans <#{RADIO_VC_ID}>."
                ),
                ephemeral=True,
            )
            return

        if getattr(current_channel, "id", None) == RADIO_VC_ID:
            await interaction.response.send_message(
                "🎧 Tu es déjà dans la session d'écoute.",
                ephemeral=True,
            )
            return

        try:
            await member.move_to(target, reason="Écoute ensemble Music 2.0")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Je n'ai pas la permission de te déplacer vers le salon musique.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            logger.exception("[listen-together] déplacement vocal impossible")
            await interaction.response.send_message(
                "❌ Impossible de rejoindre le salon musique pour le moment.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🎧 Tu as rejoint l'écoute !",
            ephemeral=True,
        )
        await self._sync_announcement()

    @tasks.loop(seconds=2.0)
    async def sync_announcement(self) -> None:
        await self._sync_announcement()

    @sync_announcement.before_loop
    async def before_sync_announcement(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    cog = Music2ListenTogetherCog(bot)
    await bot.add_cog(cog)
    bot.add_view(cog.view)
