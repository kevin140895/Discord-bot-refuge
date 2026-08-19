from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from rendering.refuge_world import season_for_month
from services.refuge_casino import CASINO_FORTUNE_NAMES, RefugeCasinoStatus
from utils.timezones import PARIS_TZ


CASINO_ROYAL_RENDERER_VERSION: Final[int] = 2
CASINO_ROYAL_SIZE: Final[tuple[int, int]] = (1280, 720)
CASINO_ROYAL_FILENAME: Final[str] = "casino-royal.png"
# Kept for import compatibility with Lot 3. The dedicated renderer no longer
# crops the small Refuge building: the Casino owns its hero composition.
CASINO_ROYAL_CROP_BOX: Final[tuple[int, int, int, int]] = (0, 0, 1280, 720)

CasinoVisualPhase = Literal[
    "dawn",
    "day",
    "golden",
    "dusk",
    "night",
    "late_night",
]

_VALID_PHASES: Final[frozenset[str]] = frozenset(
    {"dawn", "day", "golden", "dusk", "night", "late_night"}
)
_VALID_FORTUNES: Final[frozenset[str]] = frozenset(CASINO_FORTUNE_NAMES)
_PHASE_PREVIEW_HOURS: Final[dict[str, int]] = {
    "dawn": 7,
    "day": 13,
    "golden": 17,
    "dusk": 20,
    "night": 23,
    "late_night": 3,
}
_PHASE_SKY: Final[
    dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]]
] = {
    "dawn": ((37, 35, 60), (179, 103, 86)),
    "day": ((57, 91, 120), (125, 150, 154)),
    "golden": ((70, 53, 64), (199, 119, 65)),
    "dusk": ((30, 25, 51), (102, 58, 74)),
    "night": ((8, 13, 28), (24, 35, 58)),
    "late_night": ((5, 8, 20), (14, 20, 37)),
}
_FORTUNE_ACCENTS: Final[dict[str, tuple[int, int, int]]] = {
    "ruined": (112, 104, 90),
    "difficulty": (143, 111, 75),
    "stable": (190, 146, 72),
    "prosperous": (226, 178, 77),
    "insolent": (248, 211, 113),
}


@dataclass(frozen=True, slots=True)
class CasinoVisualState:
    """Pure visual read model; it never participates in roulette RNG."""

    phase: CasinoVisualPhase
    local_hour: int
    season: str
    fortune: str
    fortune_name: str
    is_open: bool
    level: int
    recent_house_net_xp: int
    world_signature: str

    @property
    def cache_key(self) -> str:
        payload = {
            "renderer_version": CASINO_ROYAL_RENDERER_VERSION,
            "phase": self.phase,
            "local_hour": self.local_hour,
            "season": self.season,
            "fortune": self.fortune,
            "is_open": self.is_open,
            "level": self.level,
            "world_signature": self.world_signature,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:24]


def casino_visual_phase_for_hour(hour: int) -> CasinoVisualPhase:
    normalized = int(hour)
    if normalized not in range(0, 24):
        raise ValueError("hour must be between 0 and 23")
    if 5 <= normalized < 9:
        return "dawn"
    if 9 <= normalized < 17:
        return "day"
    if 17 <= normalized < 19:
        return "golden"
    if 19 <= normalized < 22:
        return "dusk"
    if normalized >= 22 or normalized < 2:
        return "night"
    return "late_night"


def _local_datetime(at: datetime | None = None) -> datetime:
    current = at or datetime.now(PARIS_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=PARIS_TZ)
    return current.astimezone(PARIS_TZ)


def build_casino_visual_state(
    status: RefugeCasinoStatus,
    *,
    at: datetime | None = None,
    phase_override: str | None = None,
    fortune_override: str | None = None,
    open_override: bool | None = None,
) -> CasinoVisualState:
    local = _local_datetime(at)

    if phase_override is None:
        phase: CasinoVisualPhase = casino_visual_phase_for_hour(local.hour)
        visual_hour = local.hour
    else:
        normalized_phase = str(phase_override).strip().lower()
        if normalized_phase not in _VALID_PHASES:
            raise ValueError(f"unsupported Casino visual phase: {normalized_phase}")
        phase = normalized_phase  # type: ignore[assignment]
        visual_hour = _PHASE_PREVIEW_HOURS[normalized_phase]

    fortune = (
        status.fortune
        if fortune_override is None
        else str(fortune_override).strip().lower()
    )
    if fortune not in _VALID_FORTUNES:
        raise ValueError(f"unsupported Casino fortune: {fortune}")

    is_open = status.is_open if open_override is None else bool(open_override)
    return CasinoVisualState(
        phase=phase,
        local_hour=visual_hour,
        season=season_for_month(local.month),
        fortune=fortune,
        fortune_name=CASINO_FORTUNE_NAMES[fortune],
        is_open=is_open,
        level=max(1, int(status.level)),
        recent_house_net_xp=int(status.recent_house_net_xp),
        world_signature=str(status.render_signature),
    )


def _vertical_gradient(
    size: tuple[int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    strip = Image.new("RGB", (1, height))
    pixels = strip.load()
    assert pixels is not None
    for y in range(height):
        ratio = y / max(height - 1, 1)
        pixels[0, y] = tuple(
            int(top[index] * (1.0 - ratio) + bottom[index] * ratio)
            for index in range(3)
        )
    return strip.resize((width, height))


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    # Pillow >= 10.1 ships a scalable built-in Aileron fallback. This avoids a
    # dependency on system font files inside the production container.
    return ImageFont.load_default(size=size)


def _add_glow(
    image: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    *,
    alpha: int,
    blur: float,
) -> Image.Image:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse(box, fill=(*color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(image.convert("RGBA"), glow)


def _draw_refuge_horizon(
    draw: ImageDraw.ImageDraw,
    *,
    visual: CasinoVisualState,
) -> None:
    # The Refuge remains visible as context, but only as a distant silhouette.
    horizon = (10, 20, 24, 220)
    draw.polygon(
        (
            (0, 345),
            (120, 270),
            (230, 320),
            (360, 250),
            (500, 310),
            (640, 245),
            (780, 300),
            (930, 245),
            (1080, 300),
            (1280, 230),
            (1280, 720),
            (0, 720),
        ),
        fill=horizon,
    )

    tree_color = (9, 28, 24, 255)
    trunk_color = (25, 29, 27, 255)
    trees = (
        (80, 335, 1.0),
        (170, 380, 0.8),
        (1130, 360, 1.0),
        (1210, 320, 1.15),
    )
    for x, y, scale in trees:
        draw.rectangle(
            (x - 8 * scale, y, x + 8 * scale, y + 130 * scale),
            fill=trunk_color,
        )
        for offset, spread in ((0, 55), (35, 65), (70, 75)):
            draw.polygon(
                (
                    (x, y - 80 * scale + offset * scale),
                    (x - spread * scale, y + 25 * scale + offset * scale),
                    (x + spread * scale, y + 25 * scale + offset * scale),
                ),
                fill=tree_color,
            )

    if visual.season == "winter":
        snow = (185, 194, 194, 65)
        draw.polygon(((0, 465), (260, 440), (460, 470), (0, 515)), fill=snow)
        draw.polygon(
            ((1280, 455), (1010, 438), (850, 472), (1280, 518)), fill=snow
        )


def _draw_celestial_light(
    image: Image.Image,
    visual: CasinoVisualState,
) -> Image.Image:
    night_phase = visual.phase in {"dusk", "night", "late_night"}
    if night_phase:
        image = _add_glow(
            image,
            (980, 40, 1150, 210),
            (226, 221, 184),
            alpha=45,
            blur=34,
        )
        draw = ImageDraw.Draw(image, "RGBA")
        draw.ellipse((1028, 88, 1102, 162), fill=(226, 221, 184, 225))
    else:
        image = _add_glow(
            image,
            (950, 20, 1175, 245),
            (255, 206, 118),
            alpha=62,
            blur=38,
        )
        draw = ImageDraw.Draw(image, "RGBA")
        draw.ellipse((1022, 84, 1108, 170), fill=(255, 218, 148, 235))
    return image


def _draw_forecourt(
    image: Image.Image,
    visual: CasinoVisualState,
    accent: tuple[int, int, int],
) -> Image.Image:
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 470, 1280, 720), fill=(9, 13, 15, 255))
    draw.polygon(
        ((430, 720), (850, 720), (760, 540), (520, 540)),
        fill=(56, 52, 49, 255),
    )
    draw.line((465, 720, 548, 552), fill=(*accent, 115), width=2)
    draw.line((815, 720, 732, 552), fill=(*accent, 115), width=2)

    if visual.is_open:
        for x in (300, 980):
            image = _add_glow(
                image,
                (x - 72, 390, x + 72, 530),
                accent,
                alpha=38,
                blur=24,
            )

    draw = ImageDraw.Draw(image, "RGBA")
    for x in (300, 980):
        draw.rectangle((x - 4, 430, x + 4, 612), fill=(25, 23, 24, 255))
        draw.ellipse(
            (x - 19, 414, x + 19, 452),
            fill=(35, 31, 29, 255),
            outline=(*accent, 190),
            width=2,
        )
        lamp_fill = (*accent, 230) if visual.is_open else (74, 66, 57, 120)
        draw.ellipse((x - 10, 423, x + 10, 443), fill=lamp_fill)
    return image


def _draw_window(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    accent: tuple[int, int, int],
    lit: bool,
    dimmed: bool = False,
) -> None:
    draw.rounded_rectangle(
        (x - 55, y - 28, x + 55, y + 36),
        radius=8,
        fill=(16, 17, 18, 255),
        outline=(*accent, 255),
        width=3,
    )
    if lit and not dimmed:
        pane = (*accent, 235)
    elif lit:
        pane = (*accent, 105)
    else:
        pane = (71, 58, 54, 160)
    draw.rectangle((x - 42, y - 16, x + 42, y + 24), fill=pane)
    draw.line((x, y - 16, x, y + 24), fill=(75, 50, 30, 180), width=2)


def _draw_casino_facade(
    image: Image.Image,
    visual: CasinoVisualState,
    accent: tuple[int, int, int],
) -> Image.Image:
    draw = ImageDraw.Draw(image, "RGBA")
    gold = (*accent, 255)
    obsidian = (20, 19, 22, 255)
    burgundy = (73, 25, 35, 255)
    lit = visual.is_open

    # The building always starts as a recognizable private-club Casino.
    # Progression adds architectural prestige, never a different building type.
    draw.ellipse((275, 565, 1005, 670), fill=(0, 0, 0, 145))
    draw.rounded_rectangle(
        (220, 330, 1060, 590),
        radius=22,
        fill=obsidian,
        outline=gold,
        width=4,
    )
    draw.rectangle((250, 355, 1030, 565), fill=(54, 20, 28, 255))
    draw.rounded_rectangle(
        (360, 245, 920, 590),
        radius=24,
        fill=(25, 23, 27, 255),
        outline=gold,
        width=6,
    )
    draw.rectangle((385, 275, 895, 565), fill=burgundy)

    # Roofline, pediment and royal crown.
    draw.polygon(
        ((345, 270), (640, 160), (935, 270)),
        fill=(15, 15, 18, 255),
        outline=gold,
    )
    draw.line((345, 270, 640, 160, 935, 270), fill=gold, width=5)
    draw.polygon(
        ((465, 270), (640, 205), (815, 270)),
        fill=(42, 19, 26, 255),
        outline=(*accent, 220),
    )
    crown_x, crown_y = 640, 218
    draw.polygon(
        (
            (crown_x - 36, crown_y),
            (crown_x - 27, crown_y - 30),
            (crown_x - 9, crown_y - 10),
            (crown_x, crown_y - 35),
            (crown_x + 9, crown_y - 10),
            (crown_x + 27, crown_y - 30),
            (crown_x + 36, crown_y),
            (crown_x + 30, crown_y + 18),
            (crown_x - 30, crown_y + 18),
        ),
        fill=gold,
    )

    # Signage is intentionally part of the image identity, not a business label.
    draw.rounded_rectangle(
        (420, 292, 860, 352),
        radius=12,
        fill=(12, 12, 14, 250),
        outline=gold,
        width=3,
    )
    title = "CASINO DU REFUGE"
    title_font = _font(38)
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_box[2] - title_box[0]
    draw.text(
        (640 - title_width / 2, 300),
        title,
        font=title_font,
        fill=(240, 220, 176, 255),
        stroke_width=1,
        stroke_fill=(40, 26, 18, 255),
    )

    dim_west = visual.fortune == "difficulty"
    dim_many = visual.fortune == "ruined"
    for x in (285, 980, 435, 845):
        for y in (390, 475):
            dimmed = dim_many or (dim_west and x in {285, 435} and y == 390)
            _draw_window(
                draw,
                x=x,
                y=y,
                accent=accent,
                lit=lit,
                dimmed=dimmed,
            )

    # Grand portico and entrance.
    draw.rounded_rectangle(
        (495, 370, 785, 590),
        radius=18,
        fill=(18, 18, 20, 255),
        outline=gold,
        width=4,
    )
    for x in (525, 575, 705, 755):
        draw.rectangle((x - 11, 405, x + 11, 560), fill=(194, 175, 145, 255))
        draw.rectangle((x - 16, 396, x + 16, 410), fill=gold)
        draw.rectangle((x - 16, 558, x + 16, 570), fill=gold)

    draw.rounded_rectangle(
        (605, 420, 675, 565),
        radius=18,
        fill=(36, 17, 22, 255),
        outline=gold,
        width=3,
    )
    if lit:
        draw.rectangle((616, 434, 664, 552), fill=(*accent, 105))

    draw.rounded_rectangle(
        (500, 354, 780, 392),
        radius=10,
        fill=(12, 12, 14, 255),
        outline=gold,
        width=3,
    )
    subtitle = "MAISON ROYALE"
    subtitle_font = _font(24)
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_width = subtitle_box[2] - subtitle_box[0]
    draw.text(
        (640 - subtitle_width / 2, 360),
        subtitle,
        font=subtitle_font,
        fill=(230, 210, 170, 255),
    )

    for index in range(4):
        y = 570 + index * 15
        draw.polygon(
            (
                (470 - index * 22, y),
                (810 + index * 22, y),
                (835 + index * 22, y + 13),
                (445 - index * 22, y + 13),
            ),
            fill=(74 - 5 * index, 68 - 4 * index, 63 - 3 * index, 255),
            outline=(*accent, 110),
        )

    if lit:
        for x in (515, 765):
            draw.ellipse((x - 9, 584, x + 9, 602), fill=gold)
            draw.rectangle((x - 4, 598, x + 4, 650), fill=gold)
        draw.line((519, 607, 761, 607), fill=(124, 15, 28, 255), width=8)
    else:
        # Closed means shuttered entrance, not a vanished or ruined building.
        draw.rounded_rectangle(
            (587, 410, 693, 570),
            radius=12,
            fill=(15, 15, 17, 235),
            outline=(98, 84, 68, 230),
            width=3,
        )
        for y in range(425, 555, 14):
            draw.line((600, y, 680, y), fill=(75, 68, 60, 180), width=3)

    # Progression enriches the same Casino identity.
    if visual.level >= 2:
        for x in (270, 1010):
            draw.rectangle((x - 3, 245, x + 3, 340), fill=gold)
            draw.polygon(
                ((x + 3, 250), (x + 62, 268), (x + 3, 286)),
                fill=(111, 17, 32, 245),
                outline=gold,
            )
    if visual.level >= 3:
        draw.arc((545, 132, 735, 290), 200, 340, fill=gold, width=5)
    if visual.level >= 4:
        draw.rounded_rectangle(
            (175, 370, 230, 550), radius=14, fill=obsidian, outline=gold, width=3
        )
        draw.rounded_rectangle(
            (1050, 370, 1105, 550), radius=14, fill=obsidian, outline=gold, width=3
        )
    if visual.level >= 5:
        for x, y in ((335, 285), (945, 285), (385, 195), (895, 195)):
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=gold)

    return image


def _draw_fortune_details(
    image: Image.Image,
    visual: CasinoVisualState,
    accent: tuple[int, int, int],
) -> Image.Image:
    draw = ImageDraw.Draw(image, "RGBA")
    gold = (*accent, 255)

    if visual.fortune == "ruined":
        # Distress is visible but the Casino remains unmistakably prestigious.
        draw.line((382, 309, 410, 345, 393, 378), fill=(130, 125, 120, 210), width=4)
        draw.line((918, 335, 891, 365, 908, 402), fill=(130, 125, 120, 190), width=3)
        draw.rectangle((245, 430, 355, 440), fill=(84, 65, 44, 240))
        draw.rectangle((251, 470, 349, 480), fill=(84, 65, 44, 240))
    elif visual.fortune in {"prosperous", "insolent"}:
        for x in (405, 875):
            draw.rounded_rectangle(
                (x - 30, 548, x + 30, 590),
                radius=8,
                fill=(45, 30, 20, 255),
                outline=gold,
                width=2,
            )
            draw.ellipse((x - 38, 520, x + 38, 570), fill=(30, 72, 50, 255))
            draw.ellipse((x - 19, 507, x + 19, 544), fill=(41, 92, 58, 255))
        if visual.is_open:
            for x in (410, 870):
                draw.polygon(
                    ((x, 580), (x - 70, 340), (x + 15, 340)),
                    fill=(*accent, 25),
                )

    if visual.fortune == "insolent":
        for x in (380, 900):
            draw.rectangle((x, 210, x + 5, 330), fill=gold)
            draw.polygon(
                ((x + 5, 215), (x + 75, 235), (x + 5, 255)),
                fill=(120, 18, 35, 245),
                outline=gold,
            )
        for x, y in ((336, 285), (954, 285), (390, 196), (890, 190), (1040, 220)):
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=gold)

    return image


def _render_scene(visual: CasinoVisualState) -> Image.Image:
    sky_top, sky_bottom = _PHASE_SKY[visual.phase]
    image = _vertical_gradient(CASINO_ROYAL_SIZE, sky_top, sky_bottom).convert("RGBA")
    image = _draw_celestial_light(image, visual)

    draw = ImageDraw.Draw(image, "RGBA")
    _draw_refuge_horizon(draw, visual=visual)

    accent = _FORTUNE_ACCENTS[visual.fortune]
    image = _draw_forecourt(image, visual, accent)

    # A restrained architectural glow gives depth without making the image
    # dependent on expensive external assets or runtime image generation.
    if visual.is_open:
        image = _add_glow(
            image,
            (330, 185, 950, 630),
            accent,
            alpha=24 if visual.fortune in {"ruined", "difficulty"} else 40,
            blur=34,
        )

    image = _draw_casino_facade(image, visual, accent)
    image = _draw_fortune_details(image, visual, accent)

    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(
        (12, 12, 1267, 707),
        radius=26,
        outline=(*accent, 220),
        width=5,
    )
    draw.line((54, 684, 1226, 684), fill=(*accent, 100), width=2)
    return image.convert("RGB")


class CasinoRoyalRenderer:
    """Render a dedicated luxury Casino hero, independent from roulette RNG."""

    def render_png(
        self,
        status: RefugeCasinoStatus,
        visual: CasinoVisualState,
    ) -> bytes:
        # ``status`` stays part of the public renderer contract because cache
        # callers already pass it and future Lot 4 reactions may need it. Lot
        # 3.1 intentionally reads only the pure visual state.
        _ = status
        hero = _render_scene(visual)
        output = io.BytesIO()
        hero.save(output, format="PNG", optimize=False, compress_level=9)
        return output.getvalue()


casino_royal_renderer = CasinoRoyalRenderer()


__all__ = [
    "CASINO_ROYAL_CROP_BOX",
    "CASINO_ROYAL_FILENAME",
    "CASINO_ROYAL_RENDERER_VERSION",
    "CASINO_ROYAL_SIZE",
    "CasinoRoyalRenderer",
    "CasinoVisualPhase",
    "CasinoVisualState",
    "build_casino_visual_state",
    "casino_royal_renderer",
    "casino_visual_phase_for_hour",
]
