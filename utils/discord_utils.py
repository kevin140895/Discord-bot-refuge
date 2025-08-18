import logging
import discord
from discord.ext import commands


async def ensure_channel_has_message(
    bot: commands.Bot, channel_id: int, content: str
) -> None:
    """Ensure the text channel with ``channel_id`` has at least one message."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            logging.warning(
                "Impossible de récupérer le salon %s: %s", channel_id, exc
            )
            return

    try:
        async for _ in channel.history(limit=1):
            return
        await channel.send(content)
    except discord.Forbidden:
        logging.warning("Permissions insuffisantes dans %s", channel_id)
    except discord.HTTPException as exc:
        logging.warning("Erreur HTTP lors de l'envoi: %s", exc)


async def safe_channel_edit(channel: discord.abc.GuildChannel, **kwargs) -> None:
    """Edit a channel while gracefully handling Discord errors."""
    if all(getattr(channel, k, None) == v for k, v in kwargs.items()):
        logging.debug("[safe_channel_edit] no-op for %s", channel.id)
        return

    try:
        await channel.edit(**kwargs)
    except discord.NotFound:
        logging.warning("[safe_channel_edit] channel %s not found", channel.id)
    except discord.HTTPException as exc:
        logging.warning("[safe_channel_edit] edit failed for %s: %s", channel.id, exc)

