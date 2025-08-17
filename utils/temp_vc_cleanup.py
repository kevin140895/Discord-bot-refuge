import logging
import re
from typing import Iterable

import discord
from discord.ext import commands

TEMP_VC_NAME_RE = re.compile(r"^(PC|Console|Mobile|Crossplay|Chat)(?:\b.*)?$", re.I)


async def delete_untracked_temp_vcs(
    bot: commands.Bot, category_id: int, tracked_ids: Iterable[int]
) -> None:
    """Supprime les salons temporaires non répertoriés.

    Parcourt la catégorie ``category_id`` et supprime tout salon vocal dont le
    nom correspond au schéma temporaire mais dont l'identifiant n'est pas dans
    ``tracked_ids``. Les erreurs HTTP sont journalisées sans interrompre le
    traitement.
    """
    category = bot.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return

    tracked = set(tracked_ids)
    for ch in list(category.voice_channels):
        base = ch.name.split("•", 1)[0].strip()
        if TEMP_VC_NAME_RE.match(base) and ch.id not in tracked:
            if ch.members:
                continue
            try:
                await ch.delete(reason="Salon temporaire orphelin")
            except discord.HTTPException as exc:
                logging.warning("Suppression salon %s échouée: %s", ch.id, exc)
