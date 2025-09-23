import logging
import shlex
import shutil
from typing import Callable, Optional

import discord

from utils.audio import FFMPEG_BEFORE, FFMPEG_OPTIONS

logger = logging.getLogger(__name__)


async def fetch_voice_channel(
    bot: discord.Client, vc_id: int
) -> Optional[discord.VoiceChannel]:
    """Récupère le salon vocal correspondant à ``vc_id``.

    Retourne ``None`` si le salon est introuvable ou n'est pas un salon vocal.
    """
    channel = bot.get_channel(vc_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(vc_id)
        except discord.HTTPException:
            channel = None
    if not isinstance(channel, discord.VoiceChannel):
        logger.warning("Salon vocal %s introuvable", vc_id)
        return None
    return channel


async def ensure_voice(
    bot: discord.Client, vc_id: int, voice: Optional[discord.VoiceClient]
) -> Optional[discord.VoiceClient]:
    """Vérifie que ``voice`` est connecté au salon ``vc_id``.

    Connecte ou déplace le bot si nécessaire et retourne le client vocal
    résultant. En cas d'échec, ``voice`` est retourné inchangé.
    """
    channel = await fetch_voice_channel(bot, vc_id)
    if channel is None:
        return voice
    needs_connection = voice is None or not voice.is_connected()
    needs_move = (
        voice is not None
        and voice.is_connected()
        and getattr(voice.channel, "id", None) != vc_id
    )
    if needs_connection or needs_move:
        try:
            if needs_move and voice is not None:
                await voice.move_to(channel)
            else:
                voice = await channel.connect(reconnect=True)
        except discord.Forbidden:
            logger.warning(
                "Permissions insuffisantes pour se connecter au salon %s", vc_id
            )
        except discord.NotFound:
            logger.warning(
                "Salon %s introuvable lors de la connexion", vc_id
            )
        except discord.HTTPException as e:
            logger.error(
                "Erreur HTTP lors de la connexion au salon %s: %s", vc_id, e
            )
        except Exception as e:  # pragma: no cover - sécurité supplémentaire
            logger.exception(
                "Connexion au salon %s échouée: %s", vc_id, e
            )
    return voice


def play_stream(
    voice: Optional[discord.VoiceClient],
    stream_url: str,
    *,
    after: Optional[Callable[[Optional[Exception]], None]] = None,
    headers: Optional[str] = None,
) -> None:
    """Lance la lecture du flux ``stream_url`` si rien n'est joué."""
    if voice and not voice.is_playing():
        if shutil.which("ffmpeg") is None:
            logger.warning("FFmpeg introuvable: impossible de lire le flux %s", stream_url)
            return
        before_options = FFMPEG_BEFORE
        header_value = headers.strip() if headers else ""
        if header_value:
            before_options = f"{before_options} -headers {shlex.quote(header_value)}"
        source = discord.FFmpegPCMAudio(
            stream_url, before_options=before_options, options=FFMPEG_OPTIONS
        )
        voice.play(source, after=after)
