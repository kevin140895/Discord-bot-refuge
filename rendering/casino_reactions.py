from __future__ import annotations

import io
from typing import Final

from PIL import Image, ImageDraw, ImageFilter

from services.casino_reactions import CasinoReactionState


CASINO_REACTION_RENDERER_VERSION: Final[int] = 1


def _glow(
    image: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    *,
    alpha: int,
    blur: float,
) -> Image.Image:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.ellipse(box, fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(image, layer)


def _draw_people(draw: ImageDraw.ImageDraw, *, busy: bool) -> None:
    positions = [(470, 625), (520, 640), (760, 632), (805, 646)]
    if busy:
        positions.extend(
            [
                (410, 650),
                (450, 665),
                (565, 655),
                (705, 658),
                (845, 663),
                (885, 645),
            ]
        )
    for index, (x, y) in enumerate(positions):
        coat = (80, 30, 38, 230) if index % 2 == 0 else (28, 31, 36, 235)
        draw.ellipse((x - 7, y - 28, x + 7, y - 14), fill=(178, 149, 126, 230))
        draw.rounded_rectangle((x - 10, y - 14, x + 10, y + 18), radius=5, fill=coat)
        draw.line((x - 5, y + 18, x - 7, y + 34), fill=(20, 20, 23, 230), width=3)
        draw.line((x + 5, y + 18, x + 7, y + 34), fill=(20, 20, 23, 230), width=3)


def _draw_cars(draw: ImageDraw.ImageDraw, *, busy: bool) -> None:
    cars = [(180, 626, 315, 666)]
    if busy:
        cars.append((960, 620, 1110, 663))
    for x1, y1, x2, y2 in cars:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=15, fill=(30, 31, 35, 245))
        draw.polygon(
            ((x1 + 30, y1), (x1 + 57, y1 - 24), (x2 - 38, y1 - 24), (x2 - 18, y1)),
            fill=(42, 44, 49, 245),
        )
        draw.ellipse((x1 + 22, y2 - 8, x1 + 48, y2 + 14), fill=(8, 8, 10, 255))
        draw.ellipse((x2 - 48, y2 - 8, x2 - 22, y2 + 14), fill=(8, 8, 10, 255))
        draw.ellipse((x2 - 12, y1 + 12, x2 - 2, y1 + 22), fill=(235, 196, 112, 235))


def _activity_overlay(image: Image.Image, reaction: CasinoReactionState) -> Image.Image:
    if reaction.activity == "calm":
        return image
    draw = ImageDraw.Draw(image, "RGBA")
    busy = reaction.activity == "busy"
    _draw_people(draw, busy=busy)
    _draw_cars(draw, busy=busy)
    if busy:
        for x in (360, 920):
            draw.ellipse((x - 8, 600, x + 8, 616), fill=(224, 176, 75, 210))
    return image


def _green_zero_overlay(image: Image.Image) -> Image.Image:
    green = (69, 184, 105)
    image = _glow(image, (350, 220, 930, 605), green, alpha=55, blur=42)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((414, 286, 866, 360), radius=15, outline=(*green, 235), width=5)
    for x, y in ((345, 578), (385, 600), (895, 598), (935, 575), (640, 650)):
        draw.ellipse((x - 11, y - 11, x + 11, y + 11), fill=(*green, 225))
    return image


def _royal_win_overlay(image: Image.Image) -> Image.Image:
    gold = (238, 194, 83)
    burgundy = (125, 19, 39)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.polygon(((85, 690), (340, 180), (430, 205), (170, 690)), fill=(*gold, 34))
    draw.polygon(((1195, 690), (940, 180), (850, 205), (1110, 690)), fill=(*gold, 34))
    confetti = (
        (225, 190, gold),
        (300, 265, burgundy),
        (420, 185, gold),
        (525, 235, burgundy),
        (735, 205, gold),
        (855, 245, burgundy),
        (970, 190, gold),
        (1040, 285, burgundy),
        (580, 150, gold),
        (690, 135, burgundy),
    )
    for index, (x, y, color) in enumerate(confetti):
        if index % 2:
            draw.polygon(((x, y), (x + 13, y + 5), (x + 4, y + 16)), fill=(*color, 230))
        else:
            draw.rectangle((x, y, x + 9, y + 15), fill=(*color, 230))
    return image


def _players_streak_overlay(image: Image.Image) -> Image.Image:
    gold = (226, 178, 77)
    red = (151, 30, 47)
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(300, 1001, 70):
        color = gold if (x // 70) % 2 == 0 else red
        draw.ellipse((x - 6, 205, x + 6, 217), fill=(*color, 235))
    draw.arc((255, 165, 1025, 665), 205, 335, fill=(*gold, 150), width=4)
    return image


def _house_streak_overlay(image: Image.Image) -> Image.Image:
    burgundy = (96, 12, 31)
    image = _glow(image, (430, 110, 850, 490), burgundy, alpha=65, blur=55)
    shade = Image.new("RGBA", image.size, (18, 5, 12, 28))
    image = Image.alpha_composite(image, shade)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.arc((500, 120, 780, 330), 195, 345, fill=(166, 95, 74, 170), width=5)
    return image


def apply_casino_reaction_overlay(
    payload: bytes,
    reaction: CasinoReactionState,
) -> bytes:
    """Apply one deterministic visual reaction without changing game state."""

    if not reaction.is_notable:
        return payload

    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGBA")

    image = _activity_overlay(image, reaction)
    if reaction.reaction == "green_zero":
        image = _green_zero_overlay(image)
    elif reaction.reaction == "royal_win":
        image = _royal_win_overlay(image)
    elif reaction.reaction == "players_streak":
        image = _players_streak_overlay(image)
    elif reaction.reaction == "house_streak":
        image = _house_streak_overlay(image)

    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


__all__ = ["CASINO_REACTION_RENDERER_VERSION", "apply_casino_reaction_overlay"]
