from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Any, Final, Mapping

from PIL import Image, ImageDraw

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_hall import (
    RefugeHallRenderer,
    hall_scene_signature,
    refuge_hall_renderer,
)
from rendering.refuge_world import RefugeRenderContext
from services.refuge_casino import (
    CASINO_BUILDING_ID,
    CASINO_EVENTS,
    CASINO_MAX_LEVEL,
    CASINO_SECRET_EVENTS,
)


REFUGE_CASINO_RENDERER_VERSION: Final[int] = 1
CASINO_SITE_CENTER: Final[tuple[int, int]] = (900, 508)
_VALID_FORTUNES = frozenset(
    {"ruined", "difficulty", "stable", "prosperous", "insolent"}
)


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(
        max(0, min(255, int(round(channel * factor))))
        for channel in color
    )


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


def _casino_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == CASINO_BUILDING_ID
        ),
        None,
    )


def _fortune(building: RefugeBuildingState) -> str:
    value = str(building.state.get("fortune", "stable")).strip().lower()
    return value if value in _VALID_FORTUNES else "stable"


def _ids(value: Any, allowed: Mapping[str, str]) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(item) for item in value if str(item) in allowed)


def casino_scene_signature(
    state: RefugeWorldState,
    context: RefugeRenderContext,
) -> str:
    payload = {
        "base": hall_scene_signature(state, context),
        "casino_renderer_version": REFUGE_CASINO_RENDERER_VERSION,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _palette(
    context: RefugeRenderContext,
    fortune: str,
    is_open: bool,
) -> dict[str, tuple[int, int, int]]:
    wall = (119, 82, 65)
    roof = (70, 53, 54)
    trim = (159, 126, 73)
    lamp = (244, 191, 84) if is_open else (96, 89, 73)
    window = (228, 163, 78) if is_open else (60, 62, 67)

    if fortune == "ruined":
        wall = (84, 76, 70)
        roof = (58, 55, 55)
        trim = (99, 91, 76)
        lamp = (106, 77, 62) if is_open else (65, 61, 58)
        window = (105, 74, 65) if is_open else (48, 49, 51)
    elif fortune == "difficulty":
        wall = (103, 77, 68)
        trim = (127, 106, 77)
    elif fortune == "prosperous":
        wall = (133, 83, 60)
        trim = (189, 151, 72)
        lamp = (255, 207, 91) if is_open else lamp
    elif fortune == "insolent":
        wall = (142, 83, 55)
        roof = (74, 44, 50)
        trim = (218, 174, 69)
        lamp = (255, 221, 107) if is_open else (123, 108, 76)
        window = (255, 198, 88) if is_open else (75, 69, 66)

    if context.daypart == "night":
        wall = _shade(wall, 0.68)
        roof = _shade(roof, 0.72)
    elif context.daypart == "sunset":
        wall = _mix(wall, (142, 83, 66), 0.16)

    return {
        "wall": wall,
        "wall_dark": _shade(wall, 0.72),
        "roof": roof,
        "trim": trim,
        "trim_dark": _shade(trim, 0.70),
        "lamp": lamp,
        "window": window,
        "door": (62, 47, 43),
        "stone": (105, 104, 98),
        "gold": (218, 174, 69),
        "red": (116, 50, 51),
        "black": (39, 39, 42),
    }


def _shadow(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    radius_x: int,
) -> None:
    x, y = center
    draw.ellipse((x - radius_x, y - 12, x + radius_x, y + 20), fill=(44, 55, 43))


def _lamp(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    draw.line((x, y, x, y - 28), fill=palette["trim_dark"], width=3)
    draw.ellipse((x - 6, y - 34, x + 6, y - 22), fill=palette["lamp"])


def _window(
    draw: ImageDraw.ImageDraw,
    *,
    box: tuple[int, int, int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    draw.rectangle(box, fill=palette["trim_dark"])
    left, top, right, bottom = box
    draw.rectangle((left + 3, top + 3, right - 3, bottom - 3), fill=palette["window"])


def _draw_level_one(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _shadow(draw, center=(x, y + 8), radius_x=55)
    draw.rectangle((x - 48, y - 50, x + 48, y + 9), fill=palette["wall"])
    draw.polygon(
        ((x - 58, y - 49), (x, y - 80), (x + 58, y - 49)),
        fill=palette["roof"],
    )
    draw.rectangle((x - 13, y - 24, x + 13, y + 9), fill=palette["door"])
    _window(draw, box=(x - 38, y - 34, x - 20, y - 15), palette=palette)
    _window(draw, box=(x + 20, y - 34, x + 38, y - 15), palette=palette)
    draw.rectangle((x - 27, y - 59, x + 27, y - 48), fill=palette["trim"])


def _draw_level_two(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _shadow(draw, center=(x, y + 9), radius_x=70)
    draw.rectangle((x - 66, y - 58, x + 66, y + 10), fill=palette["wall"])
    draw.polygon(
        ((x - 78, y - 57), (x, y - 94), (x + 78, y - 57)),
        fill=palette["roof"],
    )
    draw.rectangle((x - 16, y - 30, x + 16, y + 10), fill=palette["door"])
    for px in (x - 43, x + 43):
        _window(draw, box=(px - 11, y - 39, px + 11, y - 15), palette=palette)
    for px in (x - 62, x + 62):
        _lamp(draw, x=px, y=y + 6, palette=palette)
    draw.arc((x - 23, y - 82, x + 23, y - 54), 180, 360, fill=palette["gold"], width=4)


def _draw_level_three(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _shadow(draw, center=(x, y + 10), radius_x=88)
    draw.rectangle((x - 84, y - 65, x + 84, y + 11), fill=palette["wall"])
    draw.rectangle((x - 58, y - 83, x + 58, y + 11), fill=palette["wall_dark"])
    draw.polygon(
        ((x - 68, y - 82), (x, y - 116), (x + 68, y - 82)),
        fill=palette["roof"],
    )
    for px in (x - 65, x - 42, x + 42, x + 65):
        draw.rectangle((px - 5, y - 58, px + 5, y + 6), fill=palette["trim"])
    draw.rectangle((x - 18, y - 34, x + 18, y + 11), fill=palette["door"])
    _window(draw, box=(x - 36, y - 71, x - 16, y - 50), palette=palette)
    _window(draw, box=(x + 16, y - 71, x + 36, y - 50), palette=palette)
    draw.ellipse((x - 17, y - 105, x + 17, y - 76), fill=palette["gold"])


def _draw_level_four(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _shadow(draw, center=(x, y + 11), radius_x=103)
    draw.rectangle((x - 99, y - 70, x + 99, y + 12), fill=palette["wall"])
    draw.rectangle((x - 66, y - 101, x + 66, y + 12), fill=palette["wall_dark"])
    draw.polygon(
        ((x - 78, y - 99), (x, y - 141), (x + 78, y - 99)),
        fill=palette["roof"],
    )
    for px in (x - 83, x - 56, x - 31, x + 31, x + 56, x + 83):
        draw.rectangle((px - 5, y - 65, px + 5, y + 7), fill=palette["trim"])
    draw.rectangle((x - 20, y - 38, x + 20, y + 12), fill=palette["door"])
    draw.ellipse((x - 27, y - 131, x + 27, y - 88), fill=palette["trim"])
    draw.ellipse((x - 19, y - 123, x + 19, y - 95), fill=palette["window"])
    for px in (x - 95, x + 95):
        _lamp(draw, x=px, y=y + 7, palette=palette)


def _draw_level_five(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _shadow(draw, center=(x, y + 12), radius_x=116)
    draw.rectangle((x - 112, y - 73, x + 112, y + 13), fill=palette["wall"])
    draw.rectangle((x - 75, y - 109, x + 75, y + 13), fill=palette["wall_dark"])
    draw.rectangle((x - 38, y - 142, x + 38, y + 13), fill=palette["wall"])
    draw.polygon(
        ((x - 52, y - 140), (x, y - 174), (x + 52, y - 140)),
        fill=palette["roof"],
    )
    for px in (x - 93, x - 64, x + 64, x + 93):
        _window(draw, box=(px - 10, y - 50, px + 10, y - 24), palette=palette)
    for px in (x - 58, x - 30, x + 30, x + 58):
        draw.rectangle((px - 5, y - 80, px + 5, y + 8), fill=palette["trim"])
    draw.rectangle((x - 21, y - 42, x + 21, y + 13), fill=palette["door"])
    draw.ellipse((x - 29, y - 163, x + 29, y - 112), fill=palette["gold"])
    draw.ellipse((x - 21, y - 155, x + 21, y - 119), fill=palette["window"])
    for px in (x - 109, x + 109):
        _lamp(draw, x=px, y=y + 8, palette=palette)


def _draw_fortune_details(
    draw: ImageDraw.ImageDraw,
    *,
    building: RefugeBuildingState,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = CASINO_SITE_CENTER
    fortune = _fortune(building)
    if fortune == "ruined":
        for dx in (-42, 12, 48):
            draw.line((x + dx, y - 52, x + dx + 15, y - 30), fill=palette["black"], width=3)
        draw.line((x - 28, y - 17, x + 27, y - 2), fill=palette["stone"], width=5)
    elif fortune == "difficulty":
        draw.rectangle((x - 7, y - 92, x + 7, y - 82), fill=palette["trim_dark"])
    elif fortune == "prosperous":
        for px in (x - 78, x + 78):
            draw.ellipse((px - 6, y - 86, px + 6, y - 74), fill=palette["gold"])
    elif fortune == "insolent":
        for px in (x - 90, x - 60, x + 60, x + 90):
            draw.ellipse((px - 6, y - 91, px + 6, y - 79), fill=palette["gold"])
        draw.polygon(
            ((x - 19, y - 180), (x - 11, y - 194), (x, y - 183), (x + 11, y - 194), (x + 19, y - 180)),
            fill=palette["gold"],
        )


def _draw_history_details(
    draw: ImageDraw.ImageDraw,
    *,
    building: RefugeBuildingState,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = CASINO_SITE_CENTER
    last = building.state.get("last_jackpot")
    if isinstance(last, Mapping):
        try:
            tier = int(last.get("tier", 0))
        except (TypeError, ValueError):
            tier = 0
        if tier in {500, 1000}:
            color = palette["gold"] if tier == 1000 else palette["trim"]
            draw.polygon(
                ((x + 82, y - 112), (x + 90, y - 99), (x + 82, y - 86), (x + 74, y - 99)),
                fill=color,
            )
            if tier == 1000:
                draw.ellipse((x + 76, y - 105, x + 88, y - 93), fill=(231, 237, 230))

    events = _ids(building.state.get("casino_events", ()), CASINO_EVENTS)
    if "grand_heist" in events:
        sx, sy = x - 96, y + 3
        draw.rectangle((sx - 16, sy - 22, sx + 16, sy + 8), fill=palette["stone"])
        draw.ellipse((sx - 7, sy - 13, sx + 7, sy + 1), outline=palette["black"], width=3)
        draw.line((sx + 2, sy - 9, sx + 18, sy - 25), fill=palette["red"], width=3)
    if "black_night" in events:
        draw.ellipse((x - 118, y - 91, x - 96, y - 69), fill=palette["black"])
    if "break_in" in events:
        for dx in (-9, 0, 9):
            draw.line((x + dx, y - 34, x + dx, y + 7), fill=palette["stone"], width=3)
    if "house_always_wins" in events:
        for index in range(4):
            px = x + 102 + index * 4
            py = y + 4 - index * 5
            draw.ellipse((px - 8, py - 4, px + 8, py + 4), fill=palette["gold"])


def _draw_secret_details(
    draw: ImageDraw.ImageDraw,
    *,
    building: RefugeBuildingState,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = CASINO_SITE_CENTER
    secrets = _ids(building.state.get("secret_events", ()), CASINO_SECRET_EVENTS)
    if "black_cat" in secrets:
        cx, cy = x - 110, y - 2
        draw.ellipse((cx - 12, cy - 9, cx + 12, cy + 8), fill=palette["black"])
        draw.polygon(((cx - 9, cy - 7), (cx - 5, cy - 18), (cx, cy - 7)), fill=palette["black"])
        draw.polygon(((cx + 3, cy - 7), (cx + 8, cy - 18), (cx + 11, cy - 6)), fill=palette["black"])
        draw.ellipse((cx - 5, cy - 2, cx - 2, cy + 1), fill=palette["gold"])
        draw.ellipse((cx + 3, cy - 2, cx + 6, cy + 1), fill=palette["gold"])
    if "diamond" in secrets:
        dx, dy = x + 118, y - 52
        draw.polygon(
            ((dx, dy - 13), (dx + 12, dy), (dx, dy + 15), (dx - 12, dy)),
            fill=(181, 218, 226),
        )
    if "ghost_player" in secrets:
        gx, gy = x + 116, y + 6
        ghost = (176, 190, 187)
        draw.ellipse((gx - 12, gy - 28, gx + 12, gy - 4), fill=ghost)
        draw.rectangle((gx - 12, gy - 16, gx + 12, gy + 8), fill=ghost)
        draw.ellipse((gx - 6, gy - 18, gx - 2, gy - 14), fill=palette["black"])
        draw.ellipse((gx + 2, gy - 18, gx + 6, gy - 14), fill=palette["black"])


def draw_refuge_casino(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    context: RefugeRenderContext,
) -> None:
    building = _casino_building(state)
    if building is None or int(building.level) <= 0:
        return
    level = max(1, min(CASINO_MAX_LEVEL, int(building.level)))
    fortune = _fortune(building)
    is_open = bool(building.state.get("is_open", False))
    palette = _palette(context, fortune, is_open)

    if level == 1:
        _draw_level_one(draw, center=CASINO_SITE_CENTER, palette=palette)
    elif level == 2:
        _draw_level_two(draw, center=CASINO_SITE_CENTER, palette=palette)
    elif level == 3:
        _draw_level_three(draw, center=CASINO_SITE_CENTER, palette=palette)
    elif level == 4:
        _draw_level_four(draw, center=CASINO_SITE_CENTER, palette=palette)
    else:
        _draw_level_five(draw, center=CASINO_SITE_CENTER, palette=palette)

    _draw_fortune_details(draw, building=building, palette=palette)
    _draw_history_details(draw, building=building, palette=palette)
    _draw_secret_details(draw, building=building, palette=palette)


class RefugeCasinoRenderer:
    """Compose terrain, Fire, Hall and Casino layers deterministically."""

    def __init__(
        self,
        base_renderer: RefugeHallRenderer = refuge_hall_renderer,
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
        draw_refuge_casino(draw, state, context=render_context)
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


refuge_casino_renderer = RefugeCasinoRenderer()


__all__ = [
    "CASINO_SITE_CENTER",
    "REFUGE_CASINO_RENDERER_VERSION",
    "RefugeCasinoRenderer",
    "casino_scene_signature",
    "draw_refuge_casino",
    "refuge_casino_renderer",
]
