from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Final

from PIL import Image, ImageDraw

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_world import (
    REFUGE_CANVAS_SIZE,
    RefugeRenderContext,
    RefugeWorldRenderer,
    refuge_world_renderer as base_world_renderer,
    scene_render_signature,
)
from services.refuge_fire import (
    FIRE_BUILDING_ID,
    FIRE_MAX_LEVEL,
    FIRE_SECRET_EVENTS,
)


REFUGE_FIRE_RENDERER_VERSION: Final[int] = 1
FIRE_SITE_CENTER: Final[tuple[int, int]] = (644, 420)
_VALID_INTENSITIES = frozenset({"low", "normal", "high"})


def _mix(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    clamped = max(0.0, min(1.0, ratio))
    return tuple(
        int(round(a + (b - a) * clamped))
        for a, b in zip(left, right)
    )


def _shade(
    color: tuple[int, int, int],
    factor: float,
) -> tuple[int, int, int]:
    return tuple(
        max(0, min(255, int(round(channel * factor))))
        for channel in color
    )


def _fire_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == FIRE_BUILDING_ID
        ),
        None,
    )


def _intensity(building: RefugeBuildingState) -> str:
    value = str(building.state.get("intensity", "low")).strip().lower()
    return value if value in _VALID_INTENSITIES else "low"


def _secret_ids(building: RefugeBuildingState) -> frozenset[str]:
    raw = building.state.get("secret_events", ())
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        str(value)
        for value in raw
        if str(value) in FIRE_SECRET_EVENTS
    )


def fire_scene_signature(
    state: RefugeWorldState,
    context: RefugeRenderContext,
) -> str:
    payload = {
        "base": scene_render_signature(state, context),
        "fire_renderer_version": REFUGE_FIRE_RENDERER_VERSION,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _draw_glow(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    intensity: str,
    daypart: str,
    ground_color: tuple[int, int, int],
) -> None:
    x, y = center
    base_radius = {"low": 25, "normal": 39, "high": 56}[intensity]
    if daypart == "night":
        base_radius += 15
    elif daypart == "sunset":
        base_radius += 7

    ratios = (0.12, 0.19, 0.28)
    radii = (base_radius + 20, base_radius + 10, base_radius)
    for radius, ratio in zip(radii, ratios):
        glow = _mix(ground_color, (247, 141, 57), ratio)
        draw.ellipse(
            (x - radius, y - radius // 2, x + radius, y + radius // 2),
            fill=glow,
        )


def _draw_stone_ring(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    radius_x: int,
    radius_y: int,
    stone_color: tuple[int, int, int],
    count: int = 10,
) -> None:
    x, y = center
    positions = (
        (-radius_x, 0),
        (-int(radius_x * 0.78), -int(radius_y * 0.55)),
        (-int(radius_x * 0.38), -radius_y),
        (int(radius_x * 0.38), -radius_y),
        (int(radius_x * 0.78), -int(radius_y * 0.55)),
        (radius_x, 0),
        (int(radius_x * 0.72), int(radius_y * 0.55)),
        (int(radius_x * 0.30), radius_y),
        (-int(radius_x * 0.30), radius_y),
        (-int(radius_x * 0.72), int(radius_y * 0.55)),
        (0, -radius_y - 2),
        (0, radius_y + 2),
    )
    for dx, dy in positions[: max(1, min(len(positions), count))]:
        width = 9
        height = 5
        draw.ellipse(
            (x + dx - width, y + dy - height, x + dx + width, y + dy + height),
            fill=stone_color,
        )


def _draw_flame(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    intensity: str,
    level: int,
) -> None:
    x, y = center
    scale = {"low": 0.78, "normal": 1.0, "high": 1.28}[intensity]
    scale *= 1.0 + (max(1, level) - 1) * 0.06
    height = int(49 * scale)
    width = int(27 * scale)

    outer = (224, 83, 35)
    middle = (244, 139, 45)
    inner = (255, 213, 91)
    draw.polygon(
        (
            (x, y - height),
            (x - width, y - int(height * 0.36)),
            (x - int(width * 0.72), y + 4),
            (x + int(width * 0.72), y + 4),
            (x + width, y - int(height * 0.36)),
        ),
        fill=outer,
    )
    draw.polygon(
        (
            (x + int(width * 0.18), y - int(height * 0.76)),
            (x - int(width * 0.58), y - int(height * 0.25)),
            (x - int(width * 0.35), y + 1),
            (x + int(width * 0.48), y + 1),
        ),
        fill=middle,
    )
    draw.polygon(
        (
            (x, y - int(height * 0.52)),
            (x - int(width * 0.26), y - int(height * 0.14)),
            (x, y),
            (x + int(width * 0.28), y - int(height * 0.14)),
        ),
        fill=inner,
    )


def _draw_logs(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    scale: float = 1.0,
) -> None:
    x, y = center
    half = int(31 * scale)
    width = max(5, int(7 * scale))
    wood = (92, 59, 36)
    cut = (157, 112, 70)
    draw.line((x - half, y + 8, x + half, y - 7), fill=wood, width=width)
    draw.line((x - half, y - 7, x + half, y + 8), fill=wood, width=width)
    draw.ellipse((x - half - 4, y + 4, x - half + 4, y + 12), fill=cut)
    draw.ellipse((x + half - 4, y + 4, x + half + 4, y + 12), fill=cut)


def _draw_bench(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    width: int,
    winter: bool,
) -> None:
    x, y = center
    wood = (91, 64, 42)
    edge = (60, 48, 39)
    draw.polygon(
        (
            (x - width, y - 5),
            (x + width, y - 5),
            (x + width - 7, y + 5),
            (x - width + 7, y + 5),
        ),
        fill=wood,
    )
    draw.line((x - width + 10, y + 5, x - width + 6, y + 15), fill=edge, width=4)
    draw.line((x + width - 10, y + 5, x + width - 6, y + 15), fill=edge, width=4)
    if winter:
        draw.line(
            (x - width + 3, y - 6, x + width - 3, y - 6),
            fill=(216, 223, 219),
            width=3,
        )


def _draw_tent(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    winter: bool,
) -> None:
    x, y = center
    canvas = (116, 83, 56)
    dark = _shade(canvas, 0.72)
    draw.polygon(((x, y - 43), (x - 34, y + 10), (x + 34, y + 10)), fill=canvas)
    draw.polygon(((x, y - 43), (x, y + 10), (x + 34, y + 10)), fill=dark)
    draw.line((x, y - 43, x, y + 10), fill=(72, 54, 39), width=3)
    if winter:
        draw.line((x - 19, y - 13, x, y - 43, x + 19, y - 13), fill=(220, 226, 221), width=4)


def _draw_lantern(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    lit: bool,
) -> None:
    x, y = center
    draw.line((x, y, x, y - 42), fill=(62, 54, 46), width=4)
    lamp = (244, 191, 92) if lit else (139, 122, 84)
    draw.rectangle((x - 7, y - 43, x + 7, y - 29), fill=lamp)
    draw.rectangle((x - 9, y - 46, x + 9, y - 42), fill=(66, 58, 48))


def _draw_secret_details(
    draw: ImageDraw.ImageDraw,
    *,
    secrets: frozenset[str],
    daypart: str,
) -> None:
    if "night_of_stars" in secrets:
        star_color = (190, 210, 218) if daypart != "night" else (232, 231, 191)
        for x, y in ((584, 438), (608, 452), (680, 452), (704, 438), (644, 464)):
            draw.polygon(
                ((x, y - 5), (x + 3, y), (x, y + 5), (x - 3, y)),
                fill=star_color,
            )

    if "first_visitor" in secrets:
        orange = (174, 91, 43)
        dark = (69, 55, 43)
        x, y = 742, 420
        draw.ellipse((x - 18, y - 10, x + 14, y + 8), fill=orange)
        draw.polygon(((x + 8, y - 7), (x + 23, y - 16), (x + 19, y + 1)), fill=orange)
        draw.polygon(((x - 14, y - 4), (x - 32, y - 15), (x - 25, y + 3)), fill=orange)
        draw.polygon(((x + 14, y - 12), (x + 17, y - 23), (x + 21, y - 11)), fill=orange)
        draw.ellipse((x + 18, y - 8, x + 21, y - 5), fill=dark)

    if "full_circle" in secrets:
        stone = (115, 112, 103)
        _draw_stone_ring(
            draw,
            center=(644, 421),
            radius_x=76,
            radius_y=27,
            stone_color=stone,
            count=12,
        )


def draw_refuge_fire(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    context: RefugeRenderContext,
) -> None:
    building = _fire_building(state)
    if building is None or int(building.level) <= 0:
        return

    level = max(1, min(FIRE_MAX_LEVEL, int(building.level)))
    intensity = _intensity(building)
    secrets = _secret_ids(building)
    x, y = FIRE_SITE_CENTER
    winter = context.season == "winter"
    dark_time = context.daypart in {"night", "sunset"}

    ground = {
        "winter": (139, 145, 134),
        "spring": (91, 126, 76),
        "summer": (77, 111, 66),
        "autumn": (116, 101, 65),
    }[context.season]
    if context.daypart == "night":
        ground = _shade(ground, 0.55)

    _draw_glow(
        draw,
        center=(x, y - 4),
        intensity=intensity,
        daypart=context.daypart,
        ground_color=ground,
    )

    if level >= 4:
        plaza = _mix(ground, (144, 132, 113), 0.66)
        draw.ellipse((x - 87, y - 36, x + 87, y + 35), fill=plaza)
        draw.ellipse(
            (x - 75, y - 29, x + 75, y + 29),
            outline=_shade(plaza, 0.72),
            width=3,
        )

    if level >= 2:
        _draw_tent(draw, center=(x - 79, y - 13), winter=winter)
        _draw_bench(draw, center=(x - 70, y + 28), width=28, winter=winter)
        _draw_bench(draw, center=(x + 70, y + 28), width=28, winter=winter)

    if level >= 3:
        _draw_bench(draw, center=(x, y + 48), width=31, winter=winter)
        _draw_lantern(draw, center=(x - 55, y - 25), lit=dark_time)
        _draw_lantern(draw, center=(x + 55, y - 25), lit=dark_time)

    if level >= 4:
        stone = (108, 105, 96)
        for px in (x - 91, x + 91):
            draw.rectangle((px - 7, y - 44, px + 7, y + 8), fill=stone)
            banner = (112, 57, 43) if context.season != "winter" else (84, 91, 96)
            draw.polygon(
                ((px + 8, y - 40), (px + 31, y - 33), (px + 8, y - 24)),
                fill=banner,
            )

    if level >= 5:
        stone = (101, 101, 96)
        draw.rectangle((x - 43, y - 78, x - 28, y - 6), fill=stone)
        draw.rectangle((x + 28, y - 78, x + 43, y - 6), fill=stone)
        draw.arc((x - 43, y - 103, x + 43, y - 31), 180, 360, fill=stone, width=10)
        draw.ellipse((x - 50, y + 6, x + 50, y + 31), fill=(106, 101, 91))

    _draw_secret_details(draw, secrets=secrets, daypart=context.daypart)

    ring_scale = 1.0 + (level - 1) * 0.08
    _draw_stone_ring(
        draw,
        center=(x, y + 5),
        radius_x=int(34 * ring_scale),
        radius_y=int(13 * ring_scale),
        stone_color=(111, 105, 96) if not winter else (151, 155, 150),
    )
    _draw_logs(draw, center=(x, y + 5), scale=ring_scale)
    _draw_flame(
        draw,
        center=(x, y - 2),
        intensity=intensity,
        level=level,
    )

    if winter:
        draw.arc(
            (x - 42, y - 11, x + 42, y + 22),
            190,
            342,
            fill=(218, 224, 220),
            width=3,
        )


class RefugeFireRenderer:
    """Compose the REFUGE-004 terrain with the living Fire layer."""

    def __init__(
        self,
        base_renderer: RefugeWorldRenderer = base_world_renderer,
    ) -> None:
        self.base_renderer = base_renderer

    def render_png(
        self,
        state: RefugeWorldState,
        *,
        context: RefugeRenderContext | None = None,
    ) -> bytes:
        render_context = context or RefugeRenderContext.from_datetime()
        base_png = self.base_renderer.render_png(state, context=render_context)
        image = Image.open(io.BytesIO(base_png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw_refuge_fire(draw, state, context=render_context)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False, compress_level=9)
        return buffer.getvalue()

    async def render_png_async(
        self,
        state: RefugeWorldState,
        *,
        context: RefugeRenderContext | None = None,
    ) -> bytes:
        return await asyncio.to_thread(self.render_png, state, context=context)


refuge_fire_renderer = RefugeFireRenderer()


__all__ = [
    "FIRE_SITE_CENTER",
    "REFUGE_FIRE_RENDERER_VERSION",
    "RefugeFireRenderer",
    "draw_refuge_fire",
    "fire_scene_signature",
    "refuge_fire_renderer",
]
