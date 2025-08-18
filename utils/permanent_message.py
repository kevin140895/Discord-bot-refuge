import logging
from pathlib import Path
from typing import Callable, Tuple

import discord

from config import DATA_DIR
from utils.persist import atomic_write_json, read_json_safe


async def ensure_permanent_message(
    bot: discord.Client,
    channel_id: int,
    signature: str,
    build_view_and_embed: Callable[[], Tuple[discord.ui.View | None, discord.Embed | None]],
) -> discord.Message | None:
    """Ensure a message with ``signature`` exists in ``channel_id``.

    The helper tries three strategies in order:

    1. Fetch an existing message using a stored ID.
    2. Scan the channel history for a message containing ``signature``.
    3. Publish a new message and persist its ID.
    """

    path = Path(DATA_DIR) / "permanent_messages.json"
    data = read_json_safe(path)
    msg_id = data.get(signature)

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logging.warning("[perm_msg] channel %s introuvable", channel_id)
        return None

    message: discord.Message | None = None
    if msg_id:
        try:
            message = await channel.fetch_message(msg_id)  # type: ignore[arg-type]
        except discord.NotFound:
            message = None

    if message is None:
        async for m in channel.history(limit=50):
            if m.author.id == bot.user.id and signature in (m.content or ""):
                message = m
                break

    if message is None:
        view, embed = build_view_and_embed()
        message = await channel.send(signature, view=view, embed=embed)

    if data.get(signature) != message.id:
        data[signature] = message.id
        atomic_write_json(path, data)

    return message

