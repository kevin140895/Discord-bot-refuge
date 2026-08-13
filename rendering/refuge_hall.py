from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Final, Mapping

from PIL import Image, ImageDraw

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_fire import (
    RefugeFireRenderer,
    fire_scene_signature,
    refuge_fire_renderer,
)
from rendering.refuge_world import RefugeRenderContext
from services.refuge_hall import (
    HALL_BUILDING_ID,
    HALL_MAX_LEVEL,
    HALL_SECRET_EVENTS,
)


REFUGE_HALL_RENDERER_VERSION: Final[int] = 1
HALL_SITE_CENTER: Final[tuple[int, int]] = (414, 512)


def _shade(
    color: tuple[int, int, int],
    factor: float,
) -> tuple[int, int, int]:
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
        for a, b in zip(left, right, strict=False)
    )


def _hall_building(state: RefugeWorldState) -> RefugeBuildingState | None:
    return next(
        (
            building
            for building in state.buildings
            if building.building_id == HALL_BUILDING_ID
        ),
        None,
    )


def _state_list(
    building: RefugeBuildingState,
    key: str,
) -> tuple[Mapping[str, object], ...]:
    raw = building.state.get(key, ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _secret_ids(building: RefugeBuildingState) -> frozenset[str]:
    raw = building.state.get("secret_events", ())
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        str(value)
        for value in raw
        if str(value) in HALL_SECRET_EVENTS
    )


def hall_scene_signature(
    state: RefugeWorldState,
    context: RefugeRenderContext,
) -> str:
    payload = {
        "base": fire_scene_signature(state, context),
        "hall_renderer_version": REFUGE_HALL_RENDERER_VERSION,
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
) -> dict[str, tuple[int, int, int]]:
    stone = {
        "winter": (137, 141, 139),
        "spring": (139, 132, 112),
        "summer": (134, 126, 106),
        "autumn": (139, 116, 87),
    }[context.season]
    timber = {
        "winter": (91, 78, 64),
        "spring": (106, 79, 55),
        "summer": (101, 72, 48),
        "autumn": (104, 68, 43),
    }[context.season]
    roof = {
        "winter": (79, 83, 84),
        "spring": (82, 72, 59),
        "summer": (78, 65, 52),
        "autumn": (92, 62, 46),
    }[context.season]
    if context.daypart == "night":
        stone = _shade(stone, 0.70)
        timber = _shade(timber, 0.65)
        roof = _shade(roof, 0.62)
    elif context.daypart == "sunset":
        stone = _mix(stone, (151, 98, 70), 0.14)
        timber = _mix(timber, (139, 77, 49), 0.12)

    return {
        "stone": stone,
        "stone_dark": _shade(stone, 0.72),
        "stone_light": _mix(stone, (205, 196, 170), 0.22),
        "timber": timber,
        "timber_dark": _shade(timber, 0.70),
        "roof": roof,
        "roof_dark": _shade(roof, 0.72),
        "window": (
            (236, 184, 89)
            if context.daypart in {"night", "sunset"}
            else (154, 177, 165)
        ),
        "gold": (
            (220, 175, 76)
            if context.daypart != "night"
            else (187, 145, 65)
        ),
        "banner": (
            (110, 55, 52)
            if context.season != "winter"
            else (74, 83, 91)
        ),
        "ink": (52, 45, 40),
    }


def _draw_shadow(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
) -> None:
    draw.ellipse(
        (x - width, y - 13, x + width, y + 19),
        fill=(39, 49, 40),
    )


def _draw_cabin(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: Mapping[str, tuple[int, int, int]],
    winter: bool,
) -> None:
    x, y = center
    _draw_shadow(draw, x=x, y=y + 8, width=52)
    draw.rectangle((x - 43, y - 52, x + 43, y + 10), fill=palette["timber"])
    draw.polygon(
        ((x - 54, y - 49), (x, y - 91), (x + 54, y - 49)),
        fill=palette["roof"],
    )
    draw.rectangle((x - 12, y - 21, x + 12, y + 10), fill=palette["timber_dark"])
    draw.rectangle((x - 32, y - 31, x - 17, y - 14), fill=palette["window"])
    draw.rectangle((x + 17, y - 31, x + 32, y - 14), fill=palette["window"])
    draw.rectangle((x - 17, y - 3, x + 17, y + 5), fill=palette["stone_light"])
    if winter:
        draw.line((x - 45, y - 52, x, y - 88, x + 45, y - 52), fill=(219, 225, 221), width=4)


def _draw_level_two(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: Mapping[str, tuple[int, int, int]],
    winter: bool,
) -> None:
    x, y = center
    _draw_shadow(draw, x=x, y=y + 10, width=67)
    draw.rectangle((x - 62, y - 63, x + 62, y + 11), fill=palette["stone"])
    draw.rectangle((x - 54, y - 55, x + 54, y + 11), fill=palette["timber"])
    draw.polygon(
        ((x - 73, y - 60), (x, y - 104), (x + 73, y - 60)),
        fill=palette["roof"],
    )
    draw.rectangle((x - 14, y - 26, x + 14, y + 11), fill=palette["timber_dark"])
    for px in (x - 38, x + 38):
        draw.rectangle((px - 9, y - 37, px + 9, y - 15), fill=palette["window"])
    gold = palette["gold"]
    draw.rectangle((x - 3, y - 79, x + 3, y - 67), fill=gold)
    draw.arc((x - 17, y - 92, x + 17, y - 68), 0, 180, fill=gold, width=4)
    draw.line((x - 14, y - 88, x - 24, y - 82), fill=gold, width=3)
    draw.line((x + 14, y - 88, x + 24, y - 82), fill=gold, width=3)
    if winter:
        draw.line((x - 63, y - 62, x, y - 101, x + 63, y - 62), fill=(219, 225, 221), width=4)


def _draw_level_three(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: Mapping[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _draw_shadow(draw, x=x, y=y + 12, width=79)
    draw.rectangle((x - 76, y - 66, x + 76, y + 12), fill=palette["stone"])
    draw.rectangle((x - 50, y - 82, x + 50, y + 12), fill=palette["stone_light"])
    draw.polygon(
        ((x - 58, y - 82), (x, y - 116), (x + 58, y - 82)),
        fill=palette["roof"],
    )
    for px in (x - 58, x - 34, x + 34, x + 58):
        draw.rectangle((px - 5, y - 58, px + 5, y + 7), fill=palette["stone_dark"])
        draw.rectangle((px - 8, y - 62, px + 8, y - 57), fill=palette["stone_light"])
    draw.rectangle((x - 16, y - 30, x + 16, y + 12), fill=palette["timber_dark"])
    draw.rectangle((x - 25, y - 72, x + 25, y - 54), fill=palette["banner"])


def _draw_level_four(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: Mapping[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _draw_shadow(draw, x=x, y=y + 13, width=92)
    draw.rectangle((x - 88, y - 72, x + 88, y + 13), fill=palette["stone"])
    draw.rectangle((x - 61, y - 99, x + 61, y + 13), fill=palette["stone_light"])
    draw.polygon(
        ((x - 73, y - 96), (x, y - 136), (x + 73, y - 96)),
        fill=palette["roof"],
    )
    for px in (x - 72, x - 45, x - 22, x + 22, x + 45, x + 72):
        draw.rectangle((px - 5, y - 67, px + 5, y + 8), fill=palette["stone_dark"])
    draw.rectangle((x - 18, y - 34, x + 18, y + 13), fill=palette["timber_dark"])
    draw.ellipse((x - 22, y - 126, x + 22, y - 84), fill=palette["stone_light"])
    draw.arc((x - 22, y - 126, x + 22, y - 84), 180, 360, fill=palette["gold"], width=4)


def _draw_level_five(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    palette: Mapping[str, tuple[int, int, int]],
) -> None:
    x, y = center
    _draw_shadow(draw, x=x, y=y + 14, width=108)
    draw.rectangle((x - 103, y - 75, x + 103, y + 14), fill=palette["stone"])
    draw.rectangle((x - 68, y - 111, x + 68, y + 14), fill=palette["stone_light"])
    draw.rectangle((x - 34, y - 139, x + 34, y + 14), fill=palette["stone"])
    draw.polygon(
        ((x - 48, y - 136), (x, y - 168), (x + 48, y - 136)),
        fill=palette["roof"],
    )
    for px in (x - 86, x - 58, x + 58, x + 86):
        draw.rectangle((px - 9, y - 52, px + 9, y - 24), fill=palette["window"])
    for px in (x - 51, x - 25, x + 25, x + 51):
        draw.rectangle((px - 5, y - 82, px + 5, y + 8), fill=palette["stone_dark"])
    draw.rectangle((x - 18, y - 39, x + 18, y + 14), fill=palette["timber_dark"])
    draw.ellipse((x - 25, y - 158, x + 25, y - 113), fill=palette["stone_light"])
    draw.arc((x - 25, y - 158, x + 25, y - 113), 180, 360, fill=palette["gold"], width=5)


def _draw_history_details(
    draw: ImageDraw.ImageDraw,
    *,
    building: RefugeBuildingState,
    palette: Mapping[str, tuple[int, int, int]],
) -> None:
    x, y = HALL_SITE_CENTER
    plaques = _state_list(building, "season_plaques")
    firsts = _state_list(building, "historical_firsts")
    gallery = _state_list(building, "gallery_markers")
    showcase = building.state.get("rare_showcase")

    for index, _item in enumerate(plaques[-5:]):
        px = x - 42 + index * 21
        draw.rectangle((px - 7, y + 20, px + 7, y + 30), fill=palette["stone_light"])
        draw.line((px - 4, y + 25, px + 4, y + 25), fill=palette["ink"], width=1)

    for index, _item in enumerate(firsts[:3]):
        px = x - 22 + index * 22
        draw.arc((px - 8, y - 151, px + 8, y - 133), 80, 280, fill=palette["gold"], width=2)

    for index, _item in enumerate(gallery[-4:]):
        px = x - 71 + index * 47
        draw.polygon(
            ((px - 8, y - 6), (px + 8, y - 6), (px + 6, y + 13), (px, y + 19), (px - 6, y + 13)),
            fill=palette["banner"],
        )

    if isinstance(showcase, Mapping):
        sx, sy = x + 92, y + 4
        draw.ellipse((sx - 22, sy - 8, sx + 22, sy + 11), fill=_mix(palette["stone"], palette["gold"], 0.28))
        draw.rectangle((sx - 9, sy - 30, sx + 9, sy + 1), fill=palette["stone_light"])
        star = palette["gold"]
        draw.polygon(
            (
                (sx, sy - 39),
                (sx + 4, sy - 31),
                (sx + 13, sy - 30),
                (sx + 6, sy - 24),
                (sx + 8, sy - 15),
                (sx, sy - 20),
                (sx - 8, sy - 15),
                (sx - 6, sy - 24),
                (sx - 13, sy - 30),
                (sx - 4, sy - 31),
            ),
            fill=star,
        )


def _draw_secret_details(
    draw: ImageDraw.ImageDraw,
    *,
    building: RefugeBuildingState,
    palette: Mapping[str, tuple[int, int, int]],
) -> None:
    secrets = _secret_ids(building)
    x, y = HALL_SITE_CENTER

    if "memory_flame" in secrets:
        fx, fy = x - 103, y + 6
        draw.rectangle((fx - 4, fy - 24, fx + 4, fy + 4), fill=palette["stone_dark"])
        draw.polygon(
            ((fx, fy - 40), (fx - 9, fy - 23), (fx, fy - 28), (fx + 9, fy - 23)),
            fill=(235, 131, 49),
        )

    if "endless_book" in secrets:
        bx, by = x + 111, y + 18
        draw.polygon(
            ((bx - 21, by - 12), (bx - 2, by - 17), (bx, by + 3), (bx - 20, by + 7)),
            fill=(115, 76, 48),
        )
        draw.polygon(
            ((bx + 21, by - 12), (bx + 2, by - 17), (bx, by + 3), (bx + 20, by + 7)),
            fill=(132, 88, 52),
        )
        draw.line((bx, by - 17, bx, by + 3), fill=palette["gold"], width=2)

    if "forgotten_crown" in secrets:
        cy = y - 178
        draw.polygon(
            (
                (x - 18, cy + 12),
                (x - 15, cy - 5),
                (x - 6, cy + 4),
                (x, cy - 9),
                (x + 6, cy + 4),
                (x + 15, cy - 5),
                (x + 18, cy + 12),
            ),
            fill=palette["gold"],
        )
        draw.rectangle((x - 18, cy + 11, x + 18, cy + 17), fill=palette["gold"])


def draw_refuge_hall(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    context: RefugeRenderContext,
) -> None:
    building = _hall_building(state)
    if building is None or int(building.level) <= 0:
        return

    level = max(1, min(HALL_MAX_LEVEL, int(building.level)))
    palette = _palette(context)
    winter = context.season == "winter"

    if level == 1:
        _draw_cabin(draw, center=HALL_SITE_CENTER, palette=palette, winter=winter)
    elif level == 2:
        _draw_level_two(draw, center=HALL_SITE_CENTER, palette=palette, winter=winter)
    elif level == 3:
        _draw_level_three(draw, center=HALL_SITE_CENTER, palette=palette)
    elif level == 4:
        _draw_level_four(draw, center=HALL_SITE_CENTER, palette=palette)
    else:
        _draw_level_five(draw, center=HALL_SITE_CENTER, palette=palette)

    _draw_history_details(draw, building=building, palette=palette)
    _draw_secret_details(draw, building=building, palette=palette)


class RefugeHallRenderer:
    """Compose the terrain, Fire and Hall layers deterministically."""

    def __init__(
        self,
        base_renderer: RefugeFireRenderer = refuge_fire_renderer,
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
        draw_refuge_hall(draw, state, context=render_context)
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


refuge_hall_renderer = RefugeHallRenderer()


__all__ = [
    "HALL_SITE_CENTER",
    "REFUGE_HALL_RENDERER_VERSION",
    "RefugeHallRenderer",
    "draw_refuge_hall",
    "hall_scene_signature",
    "refuge_hall_renderer",
]
