import logging

import discord

async def safe_respond(inter: discord.Interaction, content=None, **kwargs):
    """Safely respond to an interaction.

    If the initial response has already been sent, uses followup instead.
    Defaults to sending a simple checkmark when no content is provided.
    """
    try:
        if inter.is_expired():
            logging.debug("Interaction expirée, aucune réponse envoyée.")
            return
        if inter.response.is_done():
            await inter.followup.send(content or "✅", **kwargs)
        else:
            await inter.response.send_message(content or "✅", **kwargs)
    except discord.NotFound as e:
        logging.debug(f"Interaction inconnue: {e}")
    except Exception as e:
        logging.error(f"Réponse interaction échouée: {e}")
