import asyncio
import logging
from typing import Iterable, Optional

import discord
from discord.ext import commands

from config import (
    DATA_DIR,
    RADIO_RAP_FR_STREAM_URL,
    RADIO_RAP_STREAM_URL,
    RADIO_STREAM_URL,
    RADIO_TEXT_CHANNEL_ID,
    RADIO_VC_ID,
    ROCK_RADIO_STREAM_URL,
)
from storage.radio_store import RadioStore
from ui.radio_view import RadioView
from utils.voice import ensure_voice, play_stream

logger = logging.getLogger(__name__)

RADIO_CUSTOM_IDS = frozenset(
    {"radio_rap_fr", "radio_rap", "radio_rock", "radio_hiphop"}
)


def _collect_component_custom_ids(components: Iterable[object]) -> set[str]:
    """Collect custom IDs from legacy or nested Components V2 trees."""
    found: set[str] = set()
    stack = list(components)
    while stack:
        component = stack.pop()
        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str):
            found.add(custom_id)

        children = getattr(component, "children", None)
        if children:
            stack.extend(children)

        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            stack.append(accessory)
    return found


def _is_radio_message(message: discord.Message) -> bool:
    """Identify both the legacy radio panel and its Components V2 replacement."""
    custom_ids = _collect_component_custom_ids(getattr(message, "components", []))
    return RADIO_CUSTOM_IDS.issubset(custom_ids)


class RadioCog(commands.Cog):
    """Lit un flux radio dans un salon vocal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.vc_id = RADIO_VC_ID
        self.stream_url: Optional[str] = RADIO_STREAM_URL
        self.voice: Optional[discord.VoiceClient] = None
        # Task used to schedule reconnection after the stream ends.
        # ``Player.after`` callbacks are executed in a different thread
        # than the bot's event loop, so we store the future returned by
        # ``asyncio.run_coroutine_threadsafe`` instead of an ``asyncio.Task``.
        self._reconnect_task: Optional[asyncio.Future] = None
        self._previous_stream: Optional[str] = None
        self.store = RadioStore(data_dir=DATA_DIR)

    async def cog_load(self) -> None:
        """Connecte la radio si le bot est déjà prêt lors du chargement du cog."""
        if self.bot.is_ready():
            text_channel = self.bot.get_channel(RADIO_TEXT_CHANNEL_ID)
            if isinstance(text_channel, discord.abc.Messageable):
                await self._ensure_radio_message(text_channel)
            await self._connect_and_play()

    async def _connect_and_play(self) -> None:
        if not self.stream_url:
            logger.debug("Radio suspendue: aucune reconnexion à effectuer")
            return
        self.voice = await ensure_voice(self.bot, self.vc_id, self.voice)
        if self.voice is None:
            logger.warning(
                "Connexion au salon vocal échouée, nouvelle tentative planifiée"
            )
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(
                    self._delayed_reconnect()
                )
            return
        play_stream(
            self.voice,
            self.stream_url,
            after=self._after_play,
            headers=None,
        )

    def _after_play(self, error: Optional[Exception]) -> None:
        if error:
            logger.warning("Erreur de lecture radio: %s", error)
        # Music2 suspend volontairement la radio en mettant ``stream_url`` à
        # ``None``. Le callback FFmpeg de la station arrêtée arrive ensuite :
        # il ne doit surtout pas relancer l'auto-reconnect pendant la musique.
        if not self.stream_url:
            logger.debug("Fin de flux ignorée: radio suspendue")
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            # ``Player.after`` runs in the audio thread where no event loop is
            # running. Use ``run_coroutine_threadsafe`` to schedule the
            # reconnect coroutine on the bot's loop.
            self._reconnect_task = asyncio.run_coroutine_threadsafe(
                self._delayed_reconnect(), self.bot.loop
            )

    async def _delayed_reconnect(self) -> None:
        await asyncio.sleep(5)
        # A reconnect can have been queued immediately before Music2 suspended
        # the station. Re-check the state after the delay to close that race.
        if not self.stream_url:
            logger.debug("Reconnexion annulée: radio suspendue")
            return
        await self._connect_and_play()

    async def _render_radio_message(self, message: discord.Message) -> None:
        """Upgrade or refresh one existing radio message without replacing it."""
        await message.edit(
            content=None,
            embeds=[],
            attachments=[],
            view=RadioView(),
        )

    async def _ensure_radio_message(
        self, channel: discord.abc.Messageable
    ) -> None:
        stored = self.store.get_radio_message()
        channel_id = getattr(channel, "id", 0)

        # 1) Try using stored message id to avoid duplicates.
        if stored and int(stored.get("channel_id", 0)) == channel_id:
            fetch = getattr(channel, "fetch_message", None)
            if fetch:
                try:
                    msg = await fetch(int(stored.get("message_id", 0)))
                    if _is_radio_message(msg):
                        try:
                            await self._render_radio_message(msg)
                        except Exception as e:  # pragma: no cover - network issues
                            logger.warning(
                                "Impossible de mettre à jour le panneau radio: %s", e
                            )
                        return
                except Exception as e:  # pragma: no cover - network issues
                    logger.debug("Failed to fetch stored radio message: %s", e)

        # 2) Search the history for an existing radio message.
        found = None
        try:
            async for msg in channel.history(limit=None):
                if msg.author.id != self.bot.user.id:
                    continue
                if not _is_radio_message(msg):
                    continue
                if found is None:
                    found = msg
                else:
                    try:
                        await msg.delete()
                    except Exception as e:  # pragma: no cover - best effort
                        logger.debug(
                            "Failed to delete duplicate radio message: %s", e
                        )
        except Exception as e:
            logger.warning("Impossible de vérifier le message radio: %s", e)
            return

        if found:
            try:
                await self._render_radio_message(found)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("Impossible de mettre à jour le panneau radio: %s", e)
            self.store.set_radio_message(channel_id, found.id)
            return

        # 3) No message found -> create the Components V2 panel.
        try:
            msg = await channel.send(view=RadioView())
            self.store.set_radio_message(channel_id, msg.id)
        except Exception as e:
            logger.warning("Impossible d'envoyer le message radio: %s", e)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        text_channel = self.bot.get_channel(RADIO_TEXT_CHANNEL_ID)
        if isinstance(text_channel, discord.abc.Messageable):
            await self._ensure_radio_message(text_channel)
        await self._connect_and_play()

    async def _rename_for_stream(
        self, channel: discord.VoiceChannel, stream_url: str
    ) -> None:
        """Compatibility no-op: RADIO_VC_ID is no longer renamed dynamically.

        Music2 historically called this helper while restoring the previous
        station. Keeping the coroutine temporarily avoids coupling this focused
        migration to unrelated playback code while guaranteeing that no channel
        name REST write is emitted.
        """
        return None

    async def _switch_stream(
        self,
        interaction: discord.Interaction,
        stream_url: str,
        user_message: str,
    ) -> None:
        """Basculer vers un flux radio sans modifier le nom du salon."""
        is_done = getattr(interaction.response, "is_done", lambda: False)
        if not is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                logger.debug("Impossible de defer la réponse radio", exc_info=True)

        if self.stream_url == stream_url and self._previous_stream:
            self.stream_url = self._previous_stream
            self._previous_stream = None
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            await self._send_radio_response(
                interaction, "Radio changée pour la station précédente"
            )
            return

        self._previous_stream = self.stream_url
        self.stream_url = stream_url
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        await self._send_radio_response(interaction, user_message)

    async def radio_rap(self, interaction: discord.Interaction) -> None:
        await self._switch_stream(
            interaction,
            RADIO_RAP_STREAM_URL,
            "Radio changée pour rap",
        )

    async def radio_rap_fr(self, interaction: discord.Interaction) -> None:
        await self._switch_stream(
            interaction,
            RADIO_RAP_FR_STREAM_URL,
            "Radio changée pour rap FR",
        )

    async def radio_rock(self, interaction: discord.Interaction) -> None:
        await self._switch_stream(
            interaction,
            ROCK_RADIO_STREAM_URL,
            "Radio changée pour rock",
        )

    async def radio_hiphop(self, interaction: discord.Interaction) -> None:
        self.stream_url = RADIO_STREAM_URL
        self._previous_stream = None
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        await self._send_radio_response(
            interaction, "Radio changée pour la station Hip-Hop"
        )

    async def _send_radio_response(
        self, interaction: discord.Interaction, message: str
    ) -> None:
        """Répond en message éphemère quel que soit l'état de la réponse."""
        responder = (
            getattr(interaction, "followup", None) and interaction.followup.send
        )
        if responder is None:
            responder = interaction.response.send_message
        elif not getattr(interaction.response, "is_done", lambda: False)():
            responder = interaction.response.send_message

        try:
            await responder(message, ephemeral=True)
        except Exception:
            logger.warning("Réponse radio impossible", exc_info=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id == self.bot.user.id and after.channel is None:
            # Une déconnexion physique ne doit pas réveiller la radio quand
            # Music2 l'a explicitement suspendue.
            if not self.stream_url:
                return
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(
                    self._delayed_reconnect()
                )
            return

        # Previously, members with a specific role were automatically muted when
        # joining the radio channel and unmuted when leaving it. This behaviour
        # has been removed to ensure that the bot no longer alters voice states
        # based on a role.

    def cog_unload(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self.voice and self.voice.is_connected():
            asyncio.create_task(self.voice.disconnect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadioCog(bot))
