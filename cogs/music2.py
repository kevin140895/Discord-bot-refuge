from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque
from urllib.parse import urlparse

import discord
import yt_dlp
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

MUSIC2_ACCENT = discord.Colour(0x5865F2)
MUSIC2_CUSTOM_IDS = frozenset(
    {
        "music2_add",
        "music2_pause_resume",
        "music2_next",
        "music2_queue",
        "music2_now_playing",
    }
)


@dataclass(slots=True)
class MusicTrack:
    title: str
    webpage_url: str
    requester_id: int
    duration: int | None = None
    uploader: str | None = None


class AddMusicModal(discord.ui.Modal, title="Ajouter une musique"):
    query = discord.ui.TextInput(
        label="Titre ou lien",
        placeholder="Ex. Daft Punk One More Time ou un lien YouTube",
        min_length=2,
        max_length=300,
        required=True,
    )

    def __init__(self, cog: "Music2Cog") -> None:
        super().__init__(timeout=300, custom_id="music2:add_modal")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.add_track_from_interaction(interaction, str(self.query.value))


class Music2View(discord.ui.LayoutView):
    """Contrôleur Components V2 dédié à la musique personnalisée."""

    def __init__(self, cog: "Music2Cog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.refresh()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id == RADIO_TEXT_CHANNEL_ID:
            return True
        await interaction.response.send_message(
            f"❌ Utilise ce panneau dans <#{RADIO_TEXT_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return False

    def refresh(self) -> None:
        """Rebuild the visual state without changing any playback behaviour."""
        self.clear_items()
        self.add_item(self._build_container())

    def _button(
        self,
        *,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        custom_id: str,
        callback,
    ) -> discord.ui.Button:
        button = discord.ui.Button(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=custom_id,
        )
        button.callback = callback
        return button

    def _build_container(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=MUSIC2_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🎵 MUSIQUE PERSONNALISÉE\n"
                "Ajoute un titre ou un lien pour lancer une musique à la demande. "
                "La radio active est suspendue pendant la file puis reprend automatiquement."
            )
        )
        container.add_item(discord.ui.Separator())

        current = self.cog.current
        if current is None:
            current_text = (
                "### ▶️ Lecture personnalisée\n"
                "**Aucun titre en cours.**\n"
                "-# La station radio sélectionnée continue de jouer."
            )
        else:
            current_text = (
                "### ▶️ Lecture en cours\n"
                f"**{current.title}**\n"
                f"{self.cog._format_duration(current.duration)} · "
                f"demandé par <@{current.requester_id}>"
            )
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(current_text),
                accessory=self._button(
                    label="En cours",
                    emoji="🎵",
                    style=discord.ButtonStyle.secondary,
                    custom_id="music2_now_playing",
                    callback=self._show_now_playing,
                ),
            )
        )

        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        queue_size = len(self.cog.queue)
        queue_text = (
            "**Vide** · la radio reprendra automatiquement après la lecture."
            if queue_size == 0
            else f"**{queue_size} titre(s)** en attente."
        )
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"### 📃 File d'attente\n{queue_text}"
                ),
                accessory=self._button(
                    label="File",
                    emoji="📃",
                    style=discord.ButtonStyle.secondary,
                    custom_id="music2_queue",
                    callback=self._show_queue,
                ),
            )
        )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🎛️ Contrôles de lecture\n"
                "Ajoute un morceau, mets la lecture en pause ou passe directement au suivant."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                self._button(
                    label="Ajouter",
                    emoji="➕",
                    style=discord.ButtonStyle.success,
                    custom_id="music2_add",
                    callback=self._add_music,
                ),
                self._button(
                    label="Pause / Reprendre",
                    emoji="⏯️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="music2_pause_resume",
                    callback=self._pause_resume,
                ),
                self._button(
                    label="Suivant",
                    emoji="⏭️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="music2_next",
                    callback=self._next_track,
                ),
            )
        )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🔄 Retour automatique\n"
                "Quand la file personnalisée est terminée, la station radio précédente reprend automatiquement."
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                f"-# Pour contrôler la musique, rejoins d'abord <#{RADIO_VC_ID}> · Les stations restent disponibles sur le panneau Radio."
            )
        )
        return container

    async def _add_music(self, interaction: discord.Interaction) -> None:
        if not await self.cog.require_radio_listener(interaction):
            return
        await interaction.response.send_modal(AddMusicModal(self.cog))

    async def _pause_resume(self, interaction: discord.Interaction) -> None:
        await self.cog.pause_resume(interaction)

    async def _next_track(self, interaction: discord.Interaction) -> None:
        await self.cog.skip(interaction)

    async def _show_queue(self, interaction: discord.Interaction) -> None:
        await self.cog.show_queue(interaction)

    async def _show_now_playing(self, interaction: discord.Interaction) -> None:
        await self.cog.show_now_playing(interaction)


class Music2Cog(commands.Cog):
    """Couche Music 2.0 au-dessus de la radio existante, sans Lavalink."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queue: Deque[MusicTrack] = deque()
        self.current: MusicTrack | None = None
        self._radio_restore_stream: str | None = None
        self._generation = 0
        self._extract_lock = asyncio.Lock()
        self._play_lock = asyncio.Lock()
        self.store = RadioStore(data_dir=DATA_DIR)
        self.view = Music2View(self)
        self._panel_message: discord.Message | None = None

    def _radio_cog(self):
        return self.bot.get_cog("RadioCog")

    def _station_label(self, stream_url: str | None) -> str:
        return {
            RADIO_RAP_FR_STREAM_URL: "Rap FR",
            RADIO_RAP_STREAM_URL: "Rap US",
            ROCK_RADIO_STREAM_URL: "Rock",
            RADIO_STREAM_URL: "Hip-Hop",
        }.get(stream_url, "Radio")

    def _format_duration(self, duration: int | None) -> str:
        if not duration:
            return "durée inconnue"
        minutes, seconds = divmod(int(duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:d}:{seconds:02d}"

    def build_panel_embed(self) -> discord.Embed:
        """Legacy data projection kept for compatibility with existing callers/tests."""
        embed = discord.Embed(
            title="🎵 Musique personnalisée",
            description=(
                "Ajoute un titre ou un lien pour lancer une musique à la demande. "
                "La radio active est suspendue pendant la file puis reprend "
                "automatiquement quand elle est terminée."
            ),
        )
        if self.current is not None:
            embed.add_field(
                name="Lecture en cours",
                value=(
                    f"**{self.current.title}**\n"
                    f"{self._format_duration(self.current.duration)} · "
                    f"demandé par <@{self.current.requester_id}>"
                ),
                inline=False,
            )
            embed.add_field(
                name="File d'attente",
                value=f"{len(self.queue)} titre(s)",
                inline=True,
            )
        else:
            embed.add_field(
                name="Lecture personnalisée",
                value="Aucun titre en cours.",
                inline=False,
            )
            embed.add_field(name="File d'attente", value="Vide", inline=True)
        embed.set_footer(
            text="Les stations radio prédéfinies sont disponibles sur le panneau séparé."
        )
        return embed

    async def require_radio_listener(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ Action disponible uniquement sur le serveur.",
                ephemeral=True,
            )
            return False
        voice = member.voice
        if voice is None or voice.channel is None or voice.channel.id != RADIO_VC_ID:
            await interaction.response.send_message(
                f"❌ Rejoins d'abord <#{RADIO_VC_ID}> pour contrôler la musique.",
                ephemeral=True,
            )
            return False
        return True

    async def _restore_radio_panel(self, text_channel: discord.abc.Messageable) -> None:
        radio = self._radio_cog()
        ensure = getattr(radio, "_ensure_radio_message", None) if radio else None
        if callable(ensure):
            await ensure(text_channel)
            return

        stored = self.store.get_radio_message()
        if not stored or int(stored.get("channel_id", 0)) != RADIO_TEXT_CHANNEL_ID:
            return

        fetch = getattr(text_channel, "fetch_message", None)
        if not callable(fetch):
            return

        try:
            message = await fetch(int(stored.get("message_id", 0)))
            await message.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=RadioView(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            logger.exception("[music2] restauration du panneau radio impossible")

    @staticmethod
    def _component_custom_ids(message: discord.Message) -> set[str]:
        """Collect custom IDs from legacy ActionRows or nested Components V2."""
        found: set[str] = set()
        stack = list(getattr(message, "components", []))
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

    @staticmethod
    def _is_music_panel(message: discord.Message) -> bool:
        custom_ids = Music2Cog._component_custom_ids(message)
        return MUSIC2_CUSTOM_IDS.issubset(custom_ids) and "radio_hiphop" not in custom_ids

    def _refresh_panel_view(self) -> Music2View:
        self.view.refresh()
        return self.view

    async def _render_music_panel(self, message: discord.Message) -> None:
        await message.edit(
            content=None,
            embeds=[],
            attachments=[],
            view=self._refresh_panel_view(),
        )

    async def _ensure_panel(self) -> None:
        text_channel = self.bot.get_channel(RADIO_TEXT_CHANNEL_ID)
        if not isinstance(text_channel, discord.abc.Messageable):
            return

        await self._restore_radio_panel(text_channel)

        stored = self.store.get_music_message()
        fetch = getattr(text_channel, "fetch_message", None)
        if stored and int(stored.get("channel_id", 0)) == RADIO_TEXT_CHANNEL_ID and callable(fetch):
            try:
                message = await fetch(int(stored.get("message_id", 0)))
                if self._is_music_panel(message):
                    self._panel_message = message
                    await self._render_music_panel(message)
                    return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                self.store.clear_music_message()

        found: discord.Message | None = None
        try:
            history = getattr(text_channel, "history", None)
            if callable(history):
                async for message in history(limit=100):
                    if message.author.id != self.bot.user.id:
                        continue
                    if not self._is_music_panel(message):
                        continue
                    if found is None:
                        found = message
                    else:
                        try:
                            await message.delete()
                        except (discord.Forbidden, discord.HTTPException):
                            logger.debug("[music2] doublon de panneau impossible à supprimer")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[music2] historique du salon inaccessible")

        if found is None:
            try:
                found = await text_channel.send(view=self._refresh_panel_view())
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("[music2] création du panneau personnalisé impossible")
                return
        else:
            try:
                await self._render_music_panel(found)
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("[music2] mise à jour du panneau personnalisé impossible")
                return

        self._panel_message = found
        self.store.set_music_message(RADIO_TEXT_CHANNEL_ID, found.id)

    async def refresh_panel(self) -> None:
        message = self._panel_message
        if message is None:
            await self._ensure_panel()
            message = self._panel_message
        if message is None:
            return
        try:
            await self._render_music_panel(message)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            self._panel_message = None
            self.store.clear_music_message()
            logger.exception("[music2] rafraîchissement panneau impossible")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._ensure_panel()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Synchronise le panneau personnalisé après un changement de station."""
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = data.get("custom_id")
        if (
            interaction.channel_id != RADIO_TEXT_CHANNEL_ID
            or custom_id
            not in {"radio_rap_fr", "radio_rap", "radio_rock", "radio_hiphop"}
        ):
            return

        await asyncio.sleep(0.25)
        radio = self._radio_cog()
        if self.current is not None and radio is not None:
            if getattr(radio, "stream_url", None):
                self._generation += 1
                self.current = None
                self.queue.clear()
                self._radio_restore_stream = None
        await self.refresh_panel()

    def _extract_info_sync(self, target: str) -> dict:
        is_url = urlparse(target).scheme in {"http", "https"}
        lookup = target if is_url else f"ytsearch1:{target}"
        options = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(lookup, download=False)
            if info is None:
                raise RuntimeError("Aucun résultat")
            entries = info.get("entries") if hasattr(info, "get") else None
            if entries is not None:
                info = next((entry for entry in entries if entry), None)
            if info is None or not hasattr(info, "get"):
                raise RuntimeError("Aucun résultat exploitable")
            return dict(info)

    async def _extract_info(self, target: str) -> dict:
        async with self._extract_lock:
            return await asyncio.to_thread(self._extract_info_sync, target)

    def _track_from_info(self, info: dict, requester_id: int) -> MusicTrack:
        title = str(info.get("title") or "Titre inconnu")
        webpage_url = str(
            info.get("webpage_url")
            or info.get("original_url")
            or info.get("url")
            or ""
        )
        if not webpage_url:
            raise RuntimeError("Lien du média introuvable")
        duration_raw = info.get("duration")
        try:
            duration = int(duration_raw) if duration_raw is not None else None
        except (TypeError, ValueError):
            duration = None
        uploader = info.get("uploader")
        return MusicTrack(
            title=title,
            webpage_url=webpage_url,
            requester_id=requester_id,
            duration=duration,
            uploader=str(uploader) if uploader else None,
        )

    async def add_track_from_interaction(
        self, interaction: discord.Interaction, query: str
    ) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ Action disponible uniquement sur le serveur.",
                ephemeral=True,
            )
            return
        voice = member.voice
        if voice is None or voice.channel is None or voice.channel.id != RADIO_VC_ID:
            await interaction.response.send_message(
                f"❌ Rejoins d'abord <#{RADIO_VC_ID}>.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            info = await self._extract_info(query.strip())
            track = self._track_from_info(info, member.id)
        except Exception as exc:
            logger.warning("[music2] recherche impossible: %s", exc)
            await interaction.followup.send(
                "❌ Impossible de trouver ou lire ce titre.",
                ephemeral=True,
            )
            return

        self.queue.append(track)
        position = len(self.queue)
        if self.current is None:
            await self._play_next()
            position = 0

        if position == 0 and self.current is not None:
            message = f"▶️ **{track.title}** démarre."
        elif position == 0:
            message = f"⚠️ **{track.title}** n'a pas pu démarrer."
        else:
            message = f"➕ **{track.title}** ajouté à la file (position {position})."
        await interaction.followup.send(message, ephemeral=True)
        await self.refresh_panel()

    async def _resolve_stream(self, track: MusicTrack) -> tuple[str, str | None]:
        info = await self._extract_info(track.webpage_url)
        stream_url = str(info.get("url") or "")
        if not stream_url:
            formats = info.get("formats") or []
            for fmt in reversed(formats):
                if fmt and fmt.get("url"):
                    stream_url = str(fmt["url"])
                    break
        if not stream_url:
            raise RuntimeError("Flux audio introuvable")

        raw_headers = info.get("http_headers")
        headers = None
        if isinstance(raw_headers, dict) and raw_headers:
            headers = "\r\n".join(
                f"{key}: {value}" for key, value in raw_headers.items()
            ) + "\r\n"
        return stream_url, headers

    async def _suspend_radio(self) -> object:
        radio = self._radio_cog()
        if radio is None:
            raise RuntimeError("RadioCog indisponible")

        if self._radio_restore_stream is None:
            current_stream = getattr(radio, "stream_url", None)
            self._radio_restore_stream = current_stream or RADIO_STREAM_URL

        radio.stream_url = None
        voice = getattr(radio, "voice", None)
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()

        radio.voice = await ensure_voice(self.bot, RADIO_VC_ID, voice)
        if radio.voice is None:
            raise RuntimeError("Connexion vocale impossible")
        return radio

    async def _play_next(self) -> None:
        async with self._play_lock:
            if self.current is not None:
                return
            if not self.queue:
                await self._restore_radio()
                await self.refresh_panel()
                return

            track = self.queue.popleft()
            self.current = track
            self._generation += 1
            generation = self._generation

            try:
                radio = await self._suspend_radio()
                stream_url, headers = await self._resolve_stream(track)
                voice = radio.voice
                if voice and (voice.is_playing() or voice.is_paused()):
                    voice.stop()

                def after(error: Exception | None) -> None:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_track_end(generation, error),
                        self.bot.loop,
                    )

                play_stream(voice, stream_url, after=after, headers=headers)
                if not voice.is_playing() and not voice.is_paused():
                    raise RuntimeError("Le lecteur audio n'a pas démarré")
            except Exception:
                logger.exception("[music2] lecture de %s impossible", track.webpage_url)
                self.current = None
                if self.queue:
                    asyncio.create_task(self._play_next())
                else:
                    await self._restore_radio()

        await self.refresh_panel()

    async def _handle_track_end(
        self, generation: int, error: Exception | None
    ) -> None:
        if generation != self._generation:
            return
        if error:
            logger.warning("[music2] fin de piste avec erreur: %s", error)

        radio = self._radio_cog()
        if radio is not None and getattr(radio, "stream_url", None):
            self.current = None
            self.queue.clear()
            self._radio_restore_stream = None
            await self.refresh_panel()
            return

        self.current = None
        await self._play_next()

    async def _restore_radio(self) -> None:
        radio = self._radio_cog()
        if radio is None:
            self._radio_restore_stream = None
            return

        stream_url = self._radio_restore_stream or RADIO_STREAM_URL
        self._radio_restore_stream = None
        radio.stream_url = stream_url
        try:
            await radio._connect_and_play()
            channel = self.bot.get_channel(RADIO_VC_ID)
            if isinstance(channel, discord.VoiceChannel):
                await radio._rename_for_stream(channel, stream_url)
        except Exception:
            logger.exception("[music2] restauration de la radio impossible")

    async def pause_resume(self, interaction: discord.Interaction) -> None:
        if not await self.require_radio_listener(interaction):
            return
        if self.current is None:
            await interaction.response.send_message(
                "ℹ️ Aucune musique à la demande n'est en cours.",
                ephemeral=True,
            )
            return
        radio = self._radio_cog()
        voice = getattr(radio, "voice", None) if radio else None
        if voice is None:
            await interaction.response.send_message(
                "❌ Lecteur vocal indisponible.",
                ephemeral=True,
            )
            return
        if voice.is_paused():
            voice.resume()
            message = "▶️ Lecture reprise."
        elif voice.is_playing():
            voice.pause()
            message = "⏸️ Lecture en pause."
        else:
            message = "ℹ️ Le titre n'est plus en lecture."
        await interaction.response.send_message(message, ephemeral=True)
        await self.refresh_panel()

    async def skip(self, interaction: discord.Interaction) -> None:
        if not await self.require_radio_listener(interaction):
            return
        if self.current is None:
            await interaction.response.send_message(
                "ℹ️ Aucune musique à passer.",
                ephemeral=True,
            )
            return

        self._generation += 1
        self.current = None
        radio = self._radio_cog()
        voice = getattr(radio, "voice", None) if radio else None
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await interaction.response.send_message("⏭️ Titre passé.", ephemeral=True)
        await self._play_next()

    async def show_queue(self, interaction: discord.Interaction) -> None:
        lines: list[str] = []
        if self.current is not None:
            lines.append(f"▶️ **{self.current.title}**")
        for index, track in enumerate(list(self.queue)[:10], start=1):
            lines.append(f"`{index}.` {track.title}")
        if len(self.queue) > 10:
            lines.append(f"… et {len(self.queue) - 10} autre(s).")
        if not lines:
            lines.append("La file est vide : la radio est en lecture.")
        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
        )

    async def show_now_playing(self, interaction: discord.Interaction) -> None:
        if self.current is not None:
            description = (
                f"🎵 **{self.current.title}**\n"
                f"{self._format_duration(self.current.duration)} · "
                f"demandé par <@{self.current.requester_id}>"
            )
            if self.current.uploader:
                description += f"\nArtiste/chaîne : {self.current.uploader}"
        else:
            radio = self._radio_cog()
            stream_url = getattr(radio, "stream_url", RADIO_STREAM_URL) if radio else RADIO_STREAM_URL
            description = (
                "🎵 Aucune musique personnalisée en cours.\n"
                f"📻 Radio **{self._station_label(stream_url)}** active."
            )
        await interaction.response.send_message(description, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    cog = Music2Cog(bot)
    await bot.add_cog(cog)
    bot.add_view(cog.view)
