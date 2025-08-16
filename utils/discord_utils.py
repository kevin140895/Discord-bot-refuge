import logging
import discord
from discord.ext import commands

async def ensure_channel_has_message(
    bot: commands.Bot,
    channel_id: int,
    content: str,
) -> None:
    """
    Ensure the text channel with ``channel_id`` contains at least one message.

    If the channel has no history, send ``content``. Useful to guarantee
    that a channel isn't empty when the bot starts.
    """
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
