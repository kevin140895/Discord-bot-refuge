from __future__ import annotations

import discord

from rendering.casino_royal import CASINO_ROYAL_FILENAME
from services.casino_visual_cache import CasinoVisualAsset


def add_casino_visual_block(
    container: discord.ui.Container,
    asset: CasinoVisualAsset | None,
    *,
    include_media: bool,
) -> bool:
    """Append the living Casino hero without exposing roulette probabilities."""

    if asset is None:
        return False

    added = False
    if include_media:
        gallery = discord.ui.MediaGallery()
        gallery.add_item(
            media=f"attachment://{CASINO_ROYAL_FILENAME}",
            description=(
                f"Casino du Refuge · {asset.state.fortune_name} · "
                f"{'ouvert' if asset.state.is_open else 'fermé'}"
            ),
        )
        container.add_item(gallery)
        added = True

    if added:
        container.add_item(discord.ui.Separator())
    container.add_item(
        discord.ui.TextDisplay(
            "### 🏛️ Fortune de la Maison\n"
            f"**{asset.state.fortune_name}** · reflet des dernières 24 h de jeu."
        )
    )
    if asset.state.is_open and asset.reaction.is_notable:
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🎭 Ambiance du Casino\n"
                f"**{asset.reaction.label}** · réaction temporaire à l'activité des tables."
            )
        )
    return True


__all__ = ["add_casino_visual_block"]
