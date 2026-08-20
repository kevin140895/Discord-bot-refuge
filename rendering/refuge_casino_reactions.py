from __future__ import annotations

import io
from typing import Final

from PIL import Image, ImageDraw

from rendering.refuge_casino import CASINO_SITE_CENTER
from rendering.refuge_world import RefugeRenderContext
from services.casino_reactions import CasinoReactionState


REFUGE_CASINO_REACTION_RENDERER_VERSION: Final[int] = 1


def _silhouette(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    fill: tuple[int, int, int, int],
) -> None:
    draw.ellipse((x - 3, y - 12, x + 3, y - 6), fill=fill)
    draw.rectangle((x - 2, y - 6, x + 2, y + 4), fill=fill)


def _activity_overlay(
    draw: ImageDraw.ImageDraw,
    reaction: CasinoReactionState,
    *,
    context: RefugeRenderContext,
) -> None:
    if reaction.activity == "calm":
        return
    x, y = CASINO_SITE_CENTER
    alpha = 185 if context.daypart == "night" else 155
    people = [(-94, 20), (-70, 28), (67, 26)]
    if reaction.activity == "busy":
        people.extend([(-45, 30), (40, 29), (92, 18), (113, 28)])
    for dx, dy in people:
        _silhouette(
            draw,
            x=x + dx,
            y=y + dy,
            fill=(28, 24, 27, alpha),
        )

    if reaction.activity == "busy":
        # Deux phares/lanternes suggèrent l'arrivée de visiteurs sans surcharger la carte.
        for dx in (-132, 132):
            draw.ellipse(
                (x + dx - 5, y + 18, x + dx + 5, y + 28),
                fill=(238, 191, 96, 190),
            )


def _exception_overlay(
    draw: ImageDraw.ImageDraw,
    reaction: CasinoReactionState,
) -> None:
    x, y = CASINO_SITE_CENTER
    kind = reaction.reaction
    if kind == "none":
        return

    if kind == "green_zero":
        draw.ellipse(
            (x - 122, y - 178, x + 122, y + 34),
            outline=(62, 190, 105, 210),
            width=8,
        )
        for dx in (-95, -55, 55, 95):
            draw.ellipse(
                (x + dx - 6, y - 97, x + dx + 6, y - 85),
                fill=(72, 205, 114, 225),
            )
        return

    if kind == "royal_win":
        for dx in (-112, -76, -38, 38, 76, 112):
            draw.line(
                (x, y - 105, x + dx, y - 170),
                fill=(239, 194, 82, 210),
                width=4,
            )
        for dx, dy in ((-104, -140), (-66, -165), (-28, -145), (36, -161), (84, -137)):
            draw.rectangle(
                (x + dx - 3, y + dy - 3, x + dx + 3, y + dy + 3),
                fill=(238, 184, 72, 230),
            )
        return

    if kind == "players_streak":
        for dx in (-105, -70, 70, 105):
            draw.polygon(
                (
                    (x + dx, y - 148),
                    (x + dx + 12, y - 135),
                    (x + dx, y - 124),
                ),
                fill=(178, 65, 58, 220),
            )
        return

    # Série Maison : éclairage plus froid et deux fanions sombres.
    draw.arc(
        (x - 118, y - 171, x + 118, y + 12),
        190,
        350,
        fill=(92, 73, 119, 215),
        width=7,
    )
    for dx in (-108, 108):
        draw.polygon(
            (
                (x + dx, y - 144),
                (x + dx + (18 if dx < 0 else -18), y - 134),
                (x + dx, y - 123),
            ),
            fill=(63, 54, 74, 225),
        )


def apply_refuge_casino_reaction_overlay(
    payload: bytes,
    reaction: CasinoReactionState,
    *,
    context: RefugeRenderContext,
) -> bytes:
    """Project Lot 4 Casino reactions onto the Refuge map only.

    This renderer is purely visual: it never reads or mutates roulette state and
    therefore cannot influence odds, XP, payouts or legend progression.
    """

    if not reaction.is_notable:
        return payload

    base = Image.open(io.BytesIO(payload)).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _activity_overlay(draw, reaction, context=context)
    _exception_overlay(draw, reaction)
    image = Image.alpha_composite(base, overlay).convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


__all__ = [
    "REFUGE_CASINO_REACTION_RENDERER_VERSION",
    "apply_refuge_casino_reaction_overlay",
]
