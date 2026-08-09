from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Final

from PIL import Image, ImageDraw

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from rendering.refuge_casino import (
    RefugeCasinoRenderer,
    casino_scene_signature,
    refuge_casino_renderer,
)
from rendering.refuge_world import RefugeRenderContext
from services.refuge_construction import (
    CONSTRUCTION_STATUS_BUILDING,
    CONSTRUCTION_STATUS_TIE_BREAK,
    CONSTRUCTION_STATUS_VOTING,
)


REFUGE_CONSTRUCTION_RENDERER_VERSION: Final[int] = 1
CONSTRUCTION_SITE_CENTER: Final[tuple[int, int]] = (646, 616)
MONUMENT_CENTERS: Final[dict[str, tuple[int, int]]] = {
    "monument:star_observatory": (666, 292),
    "monument:memory_garden": (824, 350),
    "monument:lantern_tower": (492, 340),
}


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(channel * factor)))) for channel in color)


def _palette(context: RefugeRenderContext) -> dict[str, tuple[int, int, int]]:
    wood = (125, 89, 58)
    stone = (142, 139, 128)
    grass = (66, 113, 64)
    gold = (222, 178, 74)
    canvas = (201, 184, 145)
    if context.daypart == "night":
        wood = _shade(wood, 0.63)
        stone = _shade(stone, 0.66)
        grass = _shade(grass, 0.62)
        canvas = _shade(canvas, 0.68)
    elif context.daypart == "sunset":
        wood = (137, 85, 58)
        gold = (235, 159, 74)
    if context.season == "winter":
        grass = (111, 125, 116)
    elif context.season == "autumn":
        grass = (117, 91, 54)
    return {
        "wood": wood,
        "wood_dark": _shade(wood, 0.68),
        "stone": stone,
        "stone_dark": _shade(stone, 0.72),
        "grass": grass,
        "gold": gold,
        "canvas": canvas,
        "ink": (46, 43, 39),
        "rope": (166, 137, 84),
        "light": (246, 213, 113),
        "water": (75, 127, 146),
        "flower": (176, 94, 107),
    }


def _monument(state: RefugeWorldState, building_id: str) -> RefugeBuildingState | None:
    return next(
        (building for building in state.buildings if building.building_id == building_id),
        None,
    )


def construction_scene_signature(
    state: RefugeWorldState,
    context: RefugeRenderContext,
) -> str:
    active = state.active_construction
    monuments = [
        building.to_dict()
        for building in state.buildings
        if building.building_id.startswith("monument:")
    ]
    payload = {
        "base": casino_scene_signature(state, context),
        "renderer_version": REFUGE_CONSTRUCTION_RENDERER_VERSION,
        "active_construction": active.to_dict() if active is not None else None,
        "monuments": sorted(monuments, key=lambda item: str(item.get("building_id", ""))),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _draw_observatory(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    draw.ellipse((x - 52, y + 8, x + 52, y + 28), fill=palette["stone_dark"])
    draw.rectangle((x - 38, y - 28, x + 38, y + 13), fill=palette["stone"])
    draw.pieslice((x - 42, y - 67, x + 42, y + 5), 180, 360, fill=palette["canvas"])
    draw.line((x + 2, y - 43, x + 38, y - 73), fill=palette["wood_dark"], width=8)
    draw.ellipse((x + 31, y - 80, x + 48, y - 65), fill=palette["stone_dark"])
    draw.ellipse((x - 5, y - 46, x + 7, y - 34), fill=palette["gold"])


def _draw_memory_garden(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    draw.ellipse((x - 64, y - 19, x + 64, y + 28), fill=palette["grass"])
    draw.ellipse((x - 31, y - 11, x + 31, y + 18), fill=palette["water"])
    draw.arc((x - 40, y - 68, x + 40, y + 8), 180, 360, fill=palette["stone"], width=8)
    for dx, dy in ((-47, 2), (-30, -15), (39, -9), (52, 7), (23, 10)):
        draw.ellipse((x + dx - 5, y + dy - 5, x + dx + 5, y + dy + 5), fill=palette["flower"])


def _draw_lantern_tower(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = center
    draw.ellipse((x - 45, y + 10, x + 45, y + 27), fill=palette["stone_dark"])
    draw.polygon(
        ((x - 29, y + 12), (x - 21, y - 72), (x + 21, y - 72), (x + 29, y + 12)),
        fill=palette["stone"],
    )
    draw.polygon(((x - 34, y - 72), (x, y - 98), (x + 34, y - 72)), fill=palette["wood_dark"])
    for dx in (-18, 18):
        draw.line((x + dx, y - 57, x + dx, y - 32), fill=palette["wood_dark"], width=3)
        draw.rectangle((x + dx - 7, y - 35, x + dx + 7, y - 20), fill=palette["gold"])
        draw.rectangle((x + dx - 4, y - 32, x + dx + 4, y - 23), fill=palette["light"])


def _draw_completed_monuments(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    context: RefugeRenderContext,
) -> None:
    palette = _palette(context)
    for building_id, center in MONUMENT_CENTERS.items():
        if _monument(state, building_id) is None:
            continue
        if building_id.endswith("star_observatory"):
            _draw_observatory(draw, center, palette)
        elif building_id.endswith("memory_garden"):
            _draw_memory_garden(draw, center, palette)
        elif building_id.endswith("lantern_tower"):
            _draw_lantern_tower(draw, center, palette)


def _draw_vote_site(
    draw: ImageDraw.ImageDraw,
    *,
    tie_break: bool,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    x, y = CONSTRUCTION_SITE_CENTER
    draw.ellipse((x - 87, y - 7, x + 87, y + 27), fill=palette["stone_dark"])
    draw.rectangle((x - 55, y - 17, x + 55, y + 2), fill=palette["wood"])
    draw.line((x - 45, y + 2, x - 45, y + 21), fill=palette["wood_dark"], width=5)
    draw.line((x + 45, y + 2, x + 45, y + 21), fill=palette["wood_dark"], width=5)
    draw.rectangle((x - 28, y - 48, x + 28, y - 19), fill=palette["canvas"])
    draw.line((x - 19, y - 39, x + 18, y - 30), fill=palette["ink"], width=2)
    draw.line((x - 15, y - 28, x + 12, y - 41), fill=palette["ink"], width=2)
    flag = palette["flower"] if tie_break else palette["gold"]
    draw.line((x + 63, y - 2, x + 63, y - 53), fill=palette["wood_dark"], width=4)
    draw.polygon(((x + 65, y - 52), (x + 91, y - 43), (x + 65, y - 34)), fill=flag)


def _draw_build_site(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    palette: dict[str, tuple[int, int, int]],
) -> None:
    construction = state.active_construction
    if construction is None:
        return
    x, y = CONSTRUCTION_SITE_CENTER
    try:
        stage = max(0, min(3, int(construction.data.get("visual_stage", 0))))
    except (TypeError, ValueError):
        stage = 0

    draw.ellipse((x - 92, y - 8, x + 92, y + 29), fill=palette["stone_dark"])
    draw.rectangle((x - 62, y - 8, x + 62, y + 8), fill=palette["stone"])
    heights = (18, 38, 58, 78)
    height = heights[stage]
    for dx in (-54, 54):
        draw.line((x + dx, y + 6, x + dx, y - height), fill=palette["wood"], width=5)
    for offset in range(0, height + 1, 20):
        draw.line((x - 58, y - offset, x + 58, y - offset), fill=palette["wood"], width=4)
    draw.line((x - 54, y, x + 54, y - height), fill=palette["rope"], width=3)
    draw.line((x + 54, y, x - 54, y - height), fill=palette["rope"], width=3)
    if stage >= 1:
        draw.rectangle((x - 31, y - 30, x + 31, y + 4), fill=palette["canvas"])
    if stage >= 2:
        draw.rectangle((x - 25, y - 53, x + 25, y - 29), fill=palette["stone"])
    if stage >= 3:
        draw.polygon(((x - 31, y - 53), (x, y - 77), (x + 31, y - 53)), fill=palette["wood_dark"])


def draw_refuge_construction(
    draw: ImageDraw.ImageDraw,
    state: RefugeWorldState,
    *,
    context: RefugeRenderContext,
) -> None:
    _draw_completed_monuments(draw, state, context=context)
    construction = state.active_construction
    if construction is None:
        return
    palette = _palette(context)
    if construction.status == CONSTRUCTION_STATUS_VOTING:
        _draw_vote_site(draw, tie_break=False, palette=palette)
    elif construction.status == CONSTRUCTION_STATUS_TIE_BREAK:
        _draw_vote_site(draw, tie_break=True, palette=palette)
    elif construction.status == CONSTRUCTION_STATUS_BUILDING:
        _draw_build_site(draw, state, palette=palette)


class RefugeConstructionRenderer:
    """Compose terrain, Fire, Hall, Casino, Chantier and permanent monuments."""

    def __init__(
        self,
        base_renderer: RefugeCasinoRenderer = refuge_casino_renderer,
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
        draw_refuge_construction(draw, state, context=render_context)
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


refuge_construction_renderer = RefugeConstructionRenderer()


__all__ = [
    "CONSTRUCTION_SITE_CENTER",
    "MONUMENT_CENTERS",
    "REFUGE_CONSTRUCTION_RENDERER_VERSION",
    "RefugeConstructionRenderer",
    "construction_scene_signature",
    "draw_refuge_construction",
    "refuge_construction_renderer",
]
