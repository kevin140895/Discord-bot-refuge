from __future__ import annotations

import io
from typing import Final, Literal

from PIL import Image, ImageDraw

from rendering.refuge_fire import FIRE_SITE_CENTER
from rendering.refuge_world import RefugeRenderContext


RefugeActivityKey = Literal["endormi", "calme", "vivant", "effervescent"]
_VALID_ACTIVITY_KEYS: Final[frozenset[str]] = frozenset(
    {"endormi", "calme", "vivant", "effervescent"}
)

_SILHOUETTES: Final[tuple[tuple[int, int], ...]] = (
    (574, 429),
    (713, 431),
    (602, 457),
    (686, 460),
    (551, 454),
    (739, 456),
)
_LANTERN_POINTS: Final[tuple[tuple[int, int], ...]] = (
    (585, 388),
    (703, 388),
    (548, 420),
    (742, 420),
)


def normalize_activity_key(value: str) -> RefugeActivityKey:
    normalized = str(value).strip().lower()
    if normalized not in _VALID_ACTIVITY_KEYS:
        return "endormi"
    return normalized  # type: ignore[return-value]


def _draw_person(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    color: tuple[int, int, int, int],
) -> None:
    x, y = center
    draw.ellipse((x - 5, y - 18, x + 5, y - 8), fill=color)
    draw.rounded_rectangle((x - 7, y - 8, x + 7, y + 12), radius=4, fill=color)
    draw.line((x - 4, y + 10, x - 7, y + 20), fill=color, width=4)
    draw.line((x + 4, y + 10, x + 7, y + 20), fill=color, width=4)


def _activity_config(
    key: RefugeActivityKey,
) -> tuple[int, int, int, int]:
    """Return glow alpha/radius, silhouette count and lantern count."""

    if key == "calme":
        return 22, 58, 1, 1
    if key == "vivant":
        return 38, 76, 3, 2
    if key == "effervescent":
        return 58, 98, 6, 4
    return 8, 42, 0, 0


def apply_refuge_activity_overlay(
    png: bytes,
    *,
    activity_key: str,
    context: RefugeRenderContext,
) -> bytes:
    """Overlay cached Discord activity on the final public Refuge scene.

    The persistent world render remains the source of truth for buildings and
    progression. This layer only adds temporary presence cues around the fire.
    """

    key = normalize_activity_key(activity_key)
    glow_alpha, glow_radius, silhouette_count, lantern_count = _activity_config(key)
    if context.daypart == "night":
        glow_alpha = min(92, glow_alpha + 18)
    elif context.daypart == "sunset":
        glow_alpha = min(82, glow_alpha + 8)

    with Image.open(io.BytesIO(png)) as source:
        base = source.convert("RGBA")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x, y = FIRE_SITE_CENTER

    # A translucent community glow amplifies the existing persisted fire
    # without replacing its own level/intensity representation.
    for index, alpha_factor in enumerate((0.32, 0.55, 1.0)):
        radius = glow_radius + (2 - index) * 22
        alpha = int(round(glow_alpha * alpha_factor))
        draw.ellipse(
            (
                x - radius,
                y - radius // 2 - 7,
                x + radius,
                y + radius // 2 - 7,
            ),
            fill=(255, 145, 58, alpha),
        )

    dark_time = context.daypart in {"night", "sunset"}
    if lantern_count:
        lamp_alpha = 165 if dark_time else 82
        for lx, ly in _LANTERN_POINTS[:lantern_count]:
            draw.ellipse((lx - 14, ly - 14, lx + 14, ly + 14), fill=(255, 191, 82, lamp_alpha // 4))
            draw.ellipse((lx - 5, ly - 5, lx + 5, ly + 5), fill=(255, 215, 119, lamp_alpha))

    person_color = (44, 39, 37, 222 if dark_time else 190)
    for center in _SILHOUETTES[:silhouette_count]:
        _draw_person(draw, center=center, color=person_color)

    rendered = Image.alpha_composite(base, overlay).convert("RGB")
    buffer = io.BytesIO()
    rendered.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


__all__ = [
    "RefugeActivityKey",
    "apply_refuge_activity_overlay",
    "normalize_activity_key",
]
