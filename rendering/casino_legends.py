from __future__ import annotations

import io
from typing import Final

from PIL import Image, ImageDraw

from services.casino_legends import CasinoLegendState


CASINO_LEGEND_RENDERER_VERSION: Final[int] = 1


def _draw_public_seals(
    draw: ImageDraw.ImageDraw,
    legends: CasinoLegendState,
) -> None:
    positions = {
        "grand_heist": (96, 650),
        "black_night": (146, 650),
        "break_in": (196, 650),
        "house_always_wins": (246, 650),
    }
    accent = (205, 164, 88, 230)
    dark = (22, 17, 19, 235)
    for event_id in legends.public_events:
        position = positions.get(event_id)
        if position is None:
            continue
        x, y = position
        draw.ellipse(
            (x - 17, y - 17, x + 17, y + 17),
            fill=dark,
            outline=accent,
            width=3,
        )
        if event_id == "grand_heist":
            draw.rectangle((x - 8, y - 5, x + 8, y + 8), fill=accent)
            draw.arc((x - 8, y - 13, x + 8, y + 3), 190, 350, fill=accent, width=3)
        elif event_id == "black_night":
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=accent)
            draw.ellipse((x - 3, y - 10, x + 10, y + 5), fill=dark)
        elif event_id == "break_in":
            draw.line((x - 10, y + 8, x + 10, y - 8), fill=accent, width=4)
            draw.line((x - 10, y - 4, x - 2, y + 4), fill=accent, width=3)
            draw.line((x + 2, y - 4, x + 10, y + 4), fill=accent, width=3)
        elif event_id == "house_always_wins":
            draw.polygon(
                (
                    (x - 11, y + 6),
                    (x - 8, y - 7),
                    (x - 2, y - 1),
                    (x, y - 10),
                    (x + 3, y - 1),
                    (x + 9, y - 7),
                    (x + 11, y + 6),
                ),
                fill=accent,
            )


def _draw_black_cat(draw: ImageDraw.ImageDraw) -> None:
    body = (13, 13, 16, 245)
    eye = (205, 180, 82, 245)
    draw.ellipse((868, 186, 900, 215), fill=body)
    draw.ellipse((888, 171, 910, 193), fill=body)
    draw.polygon(((889, 176), (892, 163), (899, 177)), fill=body)
    draw.polygon(((900, 176), (908, 164), (909, 181)), fill=body)
    draw.arc((850, 181, 889, 221), 115, 300, fill=body, width=5)
    draw.ellipse((895, 181, 898, 184), fill=eye)
    draw.ellipse((902, 181, 905, 184), fill=eye)


def _draw_diamond(draw: ImageDraw.ImageDraw) -> None:
    fill = (205, 235, 242, 220)
    outline = (235, 206, 119, 245)
    points = ((366, 563), (382, 546), (398, 563), (382, 590))
    draw.polygon(points, fill=fill, outline=outline)
    draw.line((366, 563, 398, 563), fill=outline, width=2)
    draw.line((382, 546, 376, 563, 382, 590), fill=outline, width=2)
    draw.line((382, 546, 389, 563, 382, 590), fill=outline, width=2)


def _draw_ghost(draw: ImageDraw.ImageDraw) -> None:
    pale = (221, 228, 222, 85)
    draw.ellipse((949, 358, 971, 382), fill=pale)
    draw.rounded_rectangle((944, 378, 976, 430), radius=13, fill=pale)
    draw.polygon(
        ((944, 420), (950, 438), (958, 427), (966, 439), (976, 420)),
        fill=pale,
    )


def apply_casino_legend_overlay(
    payload: bytes,
    legends: CasinoLegendState,
) -> bytes:
    """Add permanent narrative traces without changing gameplay state."""

    if not legends.is_notable:
        return payload

    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGBA")

    draw = ImageDraw.Draw(image, "RGBA")
    _draw_public_seals(draw, legends)

    if "black_cat" in legends.secret_events:
        _draw_black_cat(draw)
    if "diamond" in legends.secret_events:
        _draw_diamond(draw)
    if "ghost_player" in legends.secret_events:
        _draw_ghost(draw)

    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


__all__ = [
    "CASINO_LEGEND_RENDERER_VERSION",
    "apply_casino_legend_overlay",
]
