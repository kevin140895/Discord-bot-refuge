from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from PIL import Image, ImageDraw

from models.refuge_world import RefugeWorldState
from services.refuge_world import world_render_signature
from utils.timezones import PARIS_TZ


REFUGE_CANVAS_SIZE: Final[tuple[int, int]] = (1280, 720)
REFUGE_RENDERER_VERSION: Final[int] = 2
_VALID_SEASONS = frozenset({"winter", "spring", "summer", "autumn"})
_VALID_DAYPARTS = frozenset({"morning", "day", "sunset", "night"})
CelestialBody = Literal["sun", "moon"]


@dataclass(frozen=True, slots=True)
class RefugeRenderContext:
    season: str
    daypart: str
    local_hour: int | None = None

    def __post_init__(self) -> None:
        if self.season not in _VALID_SEASONS:
            raise ValueError(f"unsupported Refuge season: {self.season}")
        if self.daypart not in _VALID_DAYPARTS:
            raise ValueError(f"unsupported Refuge daypart: {self.daypart}")
        if self.local_hour is not None and int(self.local_hour) not in range(0, 24):
            raise ValueError("local_hour must be between 0 and 23")

    @property
    def visual_hour(self) -> int:
        """Return the hourly visual slot used for sun/moon positioning."""

        if self.local_hour is not None:
            return int(self.local_hour)
        return {
            "morning": 8,
            "day": 14,
            "sunset": 20,
            "night": 1,
        }[self.daypart]

    @classmethod
    def from_datetime(cls, at: datetime | None = None) -> "RefugeRenderContext":
        current = at or datetime.now(PARIS_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=PARIS_TZ)
        local = current.astimezone(PARIS_TZ)
        return cls(
            season=season_for_month(local.month),
            daypart=daypart_for_hour(local.hour),
            local_hour=local.hour,
        )


def season_for_month(month: int) -> str:
    normalized = int(month)
    if normalized not in range(1, 13):
        raise ValueError("month must be between 1 and 12")
    if normalized in {12, 1, 2}:
        return "winter"
    if normalized in {3, 4, 5}:
        return "spring"
    if normalized in {6, 7, 8}:
        return "summer"
    return "autumn"


def daypart_for_hour(hour: int) -> str:
    normalized = int(hour)
    if normalized not in range(0, 24):
        raise ValueError("hour must be between 0 and 23")
    if 6 <= normalized < 10:
        return "morning"
    if 10 <= normalized < 18:
        return "day"
    if 18 <= normalized < 22:
        return "sunset"
    return "night"


def celestial_position_for_hour(hour: int) -> tuple[CelestialBody, int, int]:
    """Return a stylised hourly sun/moon position for the Refuge sky.

    This deliberately models a readable visual arc rather than astronomical
    coordinates. The local hour comes from Europe/Paris through
    ``RefugeRenderContext.from_datetime``.
    """

    normalized = int(hour)
    if normalized not in range(0, 24):
        raise ValueError("hour must be between 0 and 23")

    if 6 <= normalized < 22:
        progress = (normalized - 6) / 15.0
        x = int(round(150 + 980 * progress))
        y = int(round(214 - 132 * math.sin(math.pi * progress)))
        return "sun", x, y

    night_index = normalized - 22 if normalized >= 22 else normalized + 2
    progress = night_index / 7.0
    x = int(round(170 + 940 * progress))
    y = int(round(190 - 105 * math.sin(math.pi * progress)))
    return "moon", x, y


def scene_render_signature(
    state: RefugeWorldState,
    context: RefugeRenderContext,
) -> str:
    payload = {
        "renderer_version": REFUGE_RENDERER_VERSION,
        "world_signature": world_render_signature(state),
        "season": context.season,
        "daypart": context.daypart,
        "local_hour": context.visual_hour,
        "canvas": list(REFUGE_CANVAS_SIZE),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _mix(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    clamped = max(0.0, min(1.0, ratio))
    return tuple(
        int(round(a + (b - a) * clamped))
        for a, b in zip(left, right, strict=False)
    )


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(channel * factor)))) for channel in color)


def _scene_palette(context: RefugeRenderContext) -> dict[str, tuple[int, int, int]]:
    sky_by_daypart = {
        "morning": ((174, 205, 224), (244, 213, 170)),
        "day": ((111, 173, 216), (206, 226, 225)),
        "sunset": ((76, 90, 132), (235, 151, 105)),
        "night": ((18, 31, 58), (54, 67, 92)),
    }
    ground_by_season = {
        "winter": (126, 142, 132),
        "spring": (78, 126, 74),
        "summer": (63, 109, 61),
        "autumn": (111, 101, 62),
    }
    foliage_by_season = {
        "winter": (43, 74, 66),
        "spring": (61, 120, 66),
        "summer": (39, 91, 54),
        "autumn": (132, 83, 45),
    }
    sky_top, sky_bottom = sky_by_daypart[context.daypart]
    ground = ground_by_season[context.season]
    foliage = foliage_by_season[context.season]

    if context.daypart == "night":
        ground = _shade(ground, 0.56)
        foliage = _shade(foliage, 0.54)
    elif context.daypart == "sunset":
        ground = _mix(ground, (139, 88, 58), 0.23)
        foliage = _mix(foliage, (112, 66, 47), 0.18)
    elif context.daypart == "morning":
        ground = _mix(ground, (174, 169, 125), 0.10)

    return {
        "sky_top": sky_top,
        "sky_bottom": sky_bottom,
        "ground": ground,
        "ground_dark": _shade(ground, 0.78),
        "ground_light": _mix(ground, (188, 178, 132), 0.18),
        "foliage": foliage,
        "foliage_dark": _shade(foliage, 0.70),
        "trunk": (78, 57, 41) if context.daypart != "night" else (49, 42, 40),
        "path": _mix((134, 112, 80), ground, 0.20),
        "rock": _mix((112, 119, 116), ground, 0.18),
        "water": (64, 119, 146) if context.daypart != "night" else (39, 70, 96),
        "clearing": _mix(ground, (162, 143, 94), 0.20),
    }


def _draw_vertical_gradient(
    image: Image.Image,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    *,
    end_y: int,
) -> None:
    draw = ImageDraw.Draw(image)
    denominator = max(1, end_y - 1)
    for y in range(end_y):
        draw.line((0, y, image.width, y), fill=_mix(top, bottom, y / denominator))


def _draw_sky_details(
    draw: ImageDraw.ImageDraw,
    context: RefugeRenderContext,
) -> None:
    if context.daypart == "night":
        stars = (
            (88, 72),
            (154, 116),
            (244, 64),
            (318, 138),
            (406, 84),
            (508, 126),
            (598, 62),
            (704, 104),
            (792, 58),
            (884, 132),
            (970, 80),
            (1064, 116),
            (1168, 62),
            (1224, 150),
        )
        for index, (x, y) in enumerate(stars):
            radius = 2 if index % 3 else 3
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(226, 231, 220),
            )

    body, x, y = celestial_position_for_hour(context.visual_hour)
    if body == "moon":
        radius = 31
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(225, 221, 188),
        )
        draw.ellipse((x - 12, y - 5, x - 4, y + 3), fill=(198, 197, 176))
        draw.ellipse((x + 7, y + 7, x + 15, y + 15), fill=(201, 199, 177))
        draw.ellipse((x + 5, y - 16, x + 11, y - 10), fill=(205, 202, 179))
        return

    outer_radius = 43 if context.daypart == "sunset" else 40
    inner_radius = 34
    outer = (245, 184, 105) if context.daypart == "sunset" else (247, 222, 145)
    inner = (250, 196, 105) if context.daypart == "sunset" else (247, 226, 156)
    draw.ellipse(
        (x - outer_radius, y - outer_radius, x + outer_radius, y + outer_radius),
        fill=outer,
    )
    draw.ellipse(
        (x - inner_radius, y - inner_radius, x + inner_radius, y + inner_radius),
        fill=inner,
    )


def _draw_mountains(
    draw: ImageDraw.ImageDraw,
    palette: dict[str, tuple[int, int, int]],
    context: RefugeRenderContext,
) -> None:
    back = _mix(palette["ground_dark"], palette["sky_bottom"], 0.44)
    front = _mix(palette["ground_dark"], palette["sky_bottom"], 0.25)
    draw.polygon(
        ((0, 286), (170, 170), (292, 282), (456, 154), (606, 288), (0, 288)),
        fill=back,
    )
    draw.polygon(
        (
            (424, 288),
            (612, 176),
            (736, 270),
            (902, 144),
            (1088, 286),
            (1280, 204),
            (1280, 300),
            (424, 300),
        ),
        fill=front,
    )
    if context.season == "winter":
        snow = (221, 225, 218)
        draw.polygon(
            ((138, 192), (170, 170), (199, 196), (177, 189), (164, 202)),
            fill=snow,
        )
        draw.polygon(
            ((872, 169), (902, 144), (934, 174), (910, 166), (896, 184)),
            fill=snow,
        )


def _draw_tree(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    scale: float,
    palette: dict[str, tuple[int, int, int]],
    context: RefugeRenderContext,
) -> None:
    trunk_h = int(44 * scale)
    trunk_w = max(4, int(9 * scale))
    draw.rectangle(
        (x - trunk_w // 2, y - trunk_h, x + trunk_w // 2, y),
        fill=palette["trunk"],
    )
    foliage = palette["foliage"]
    dark = palette["foliage_dark"]
    widths = (44, 36, 27)
    heights = (44, 40, 36)
    offsets = (30, 55, 78)
    for index, (width, height, offset) in enumerate(
        zip(widths, heights, offsets, strict=False)
    ):
        half = int(width * scale)
        top_y = y - int((offset + height) * scale)
        base_y = y - int(offset * scale)
        fill = foliage if index != 1 else dark
        draw.polygon(((x, top_y), (x - half, base_y), (x + half, base_y)), fill=fill)
        if context.season == "winter":
            snow_y = top_y + max(3, int(8 * scale))
            snow_half = max(5, int(half * 0.56))
            draw.polygon(
                ((x, top_y), (x - snow_half, snow_y), (x + snow_half, snow_y)),
                fill=(214, 221, 214),
            )


def _draw_ground_details(
    draw: ImageDraw.ImageDraw,
    palette: dict[str, tuple[int, int, int]],
    context: RefugeRenderContext,
) -> None:
    rocks = (
        (214, 476, 14),
        (1042, 470, 18),
        (312, 616, 12),
        (978, 626, 11),
    )
    for x, y, radius in rocks:
        draw.ellipse(
            (x - radius, y - radius // 2, x + radius, y + radius // 2),
            fill=palette["rock"],
        )

    if context.season == "spring":
        flowers = (
            (262, 536),
            (334, 558),
            (952, 548),
            (1018, 572),
            (528, 616),
            (756, 608),
        )
        for index, (x, y) in enumerate(flowers):
            color = (222, 178, 193) if index % 2 else (230, 214, 128)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
    elif context.season == "autumn":
        leaves = (
            (250, 548),
            (296, 564),
            (992, 536),
            (1032, 566),
            (456, 642),
            (812, 630),
        )
        for index, (x, y) in enumerate(leaves):
            color = (160, 91, 44) if index % 2 else (190, 126, 55)
            draw.ellipse((x - 5, y - 2, x + 5, y + 2), fill=color)
    elif context.season == "winter":
        for x, y, rx, ry in (
            (420, 526, 62, 14),
            (862, 562, 70, 16),
            (614, 650, 96, 12),
        ):
            draw.ellipse(
                (x - rx, y - ry, x + rx, y + ry),
                fill=(197, 205, 198),
            )
    else:
        grass = (
            (272, 548),
            (328, 570),
            (970, 542),
            (1026, 582),
            (470, 634),
            (802, 626),
        )
        for x, y in grass:
            draw.line((x, y, x - 4, y - 11), fill=palette["foliage"])
            draw.line((x, y, x + 5, y - 9), fill=palette["foliage_dark"])


def _draw_site_pad(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius_x: int,
    radius_y: int,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    shadow = _shade(palette["clearing"], 0.78)
    draw.ellipse(
        (x - radius_x, y - radius_y + 5, x + radius_x, y + radius_y + 7),
        fill=shadow,
    )
    draw.ellipse(
        (x - radius_x, y - radius_y, x + radius_x, y + radius_y),
        fill=palette["clearing"],
    )


class RefugeWorldRenderer:
    """Render the deterministic terrain base for the Refuge world."""

    def render_png(
        self,
        state: RefugeWorldState,
        *,
        context: RefugeRenderContext | None = None,
    ) -> bytes:
        render_context = context or RefugeRenderContext.from_datetime()
        _, height = REFUGE_CANVAS_SIZE
        palette = _scene_palette(render_context)
        image = Image.new("RGB", REFUGE_CANVAS_SIZE, palette["sky_top"])
        draw = ImageDraw.Draw(image)

        _draw_vertical_gradient(
            image,
            palette["sky_top"],
            palette["sky_bottom"],
            end_y=300,
        )
        draw = ImageDraw.Draw(image)
        _draw_sky_details(draw, render_context)
        _draw_mountains(draw, palette, render_context)

        draw.polygon(
            ((0, 270), (1280, 270), (1280, height), (0, height)),
            fill=palette["ground"],
        )
        draw.polygon(
            ((0, 310), (1280, 286), (1280, 350), (0, 372)),
            fill=palette["ground_dark"],
        )

        draw.polygon(
            ((0, 472), (118, 452), (196, 500), (278, 720), (0, 720)),
            fill=palette["water"],
        )
        draw.line(
            (112, 458, 195, 506, 272, 716),
            fill=_mix(palette["water"], (215, 225, 218), 0.28),
            width=3,
        )

        draw.polygon(
            ((562, 720), (722, 720), (672, 436), (616, 436)),
            fill=palette["path"],
        )

        _draw_site_pad(draw, (644, 420), 92, 36, palette)
        _draw_site_pad(draw, (414, 512), 82, 29, palette)
        _draw_site_pad(draw, (900, 508), 88, 31, palette)
        _draw_site_pad(draw, (646, 616), 96, 32, palette)

        trees = (
            (70, 360, 0.66),
            (144, 382, 0.74),
            (222, 404, 0.82),
            (1110, 366, 0.70),
            (1192, 396, 0.78),
            (1038, 418, 0.82),
            (284, 474, 0.88),
            (1000, 484, 0.91),
            (186, 560, 1.00),
            (1084, 574, 1.02),
            (330, 652, 1.09),
            (984, 660, 1.10),
        )
        for x, y, scale in sorted(trees, key=lambda item: item[1]):
            _draw_tree(draw, x, y, scale, palette, render_context)

        _draw_ground_details(draw, palette, render_context)

        # REFUGE-004 intentionally renders only the shared terrain and reserved
        # sites. Building-specific silhouettes are layered in later stages.
        _ = state

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


refuge_world_renderer = RefugeWorldRenderer()


__all__ = [
    "CelestialBody",
    "REFUGE_CANVAS_SIZE",
    "REFUGE_RENDERER_VERSION",
    "RefugeRenderContext",
    "RefugeWorldRenderer",
    "celestial_position_for_hour",
    "daypart_for_hour",
    "refuge_world_renderer",
    "scene_render_signature",
    "season_for_month",
]
