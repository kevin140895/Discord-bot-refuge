from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal

from PIL import Image, ImageDraw

from models.refuge_world import RefugeWorldState
from rendering.refuge_casino import RefugeCasinoRenderer, refuge_casino_renderer
from rendering.refuge_world import RefugeRenderContext, daypart_for_hour, season_for_month
from services.refuge_casino import (
    CASINO_BUILDING_ID,
    CASINO_FORTUNE_NAMES,
    RefugeCasinoStatus,
)
from utils.timezones import PARIS_TZ


CASINO_ROYAL_RENDERER_VERSION: Final[int] = 1
CASINO_ROYAL_SIZE: Final[tuple[int, int]] = (1280, 720)
CASINO_ROYAL_FILENAME: Final[str] = "casino-royal.png"
CASINO_ROYAL_CROP_BOX: Final[tuple[int, int, int, int]] = (660, 290, 1140, 560)

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
_PHASE_TINTS: Final[dict[str, tuple[int, int, int, int]]] = {
    "dawn": (232, 168, 112, 20),
    "day": (255, 255, 255, 0),
    "golden": (184, 104, 52, 27),
    "dusk": (83, 42, 72, 29),
    "night": (13, 20, 46, 25),
    "late_night": (7, 12, 32, 42),
}
_FORTUNE_ACCENTS: Final[dict[str, tuple[int, int, int]]] = {
    "ruined": (105, 103, 98),
    "difficulty": (133, 104, 75),
    "stable": (181, 142, 70),
    "prosperous": (219, 172, 69),
    "insolent": (245, 205, 105),
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

    fortune = status.fortune if fortune_override is None else str(fortune_override).strip().lower()
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


def _world_with_visual_overrides(
    state: RefugeWorldState,
    visual: CasinoVisualState,
) -> RefugeWorldState:
    updated_buildings = []
    found = False
    for building in state.buildings:
        if building.building_id != CASINO_BUILDING_ID:
            updated_buildings.append(building)
            continue
        found = True
        building_state = dict(building.state)
        building_state["fortune"] = visual.fortune
        building_state["is_open"] = visual.is_open
        updated_buildings.append(replace(building, state=building_state))
    if not found:
        return state
    return replace(state, buildings=tuple(updated_buildings))


def _render_context(visual: CasinoVisualState) -> RefugeRenderContext:
    return RefugeRenderContext(
        season=visual.season,
        daypart=daypart_for_hour(visual.local_hour),
        local_hour=visual.local_hour,
    )


def _apply_treatment(image: Image.Image, visual: CasinoVisualState) -> Image.Image:
    treated = image.convert("RGBA")
    tint = _PHASE_TINTS[visual.phase]
    if tint[3] > 0:
        treated = Image.alpha_composite(
            treated,
            Image.new("RGBA", treated.size, tint),
        )
    if not visual.is_open:
        treated = Image.alpha_composite(
            treated,
            Image.new("RGBA", treated.size, (8, 8, 12, 34)),
        )

    draw = ImageDraw.Draw(treated)
    accent = _FORTUNE_ACCENTS[visual.fortune]
    width, height = treated.size
    draw.rounded_rectangle(
        (8, 8, width - 9, height - 9),
        radius=24,
        outline=(*accent, 235),
        width=6,
    )
    draw.line(
        (42, height - 32, width - 42, height - 32),
        fill=(*accent, 125),
        width=3,
    )
    return treated.convert("RGB")


class CasinoRoyalRenderer:
    """Create a dedicated 16:9 Casino hero from the canonical Refuge world."""

    def __init__(
        self,
        base_renderer: RefugeCasinoRenderer = refuge_casino_renderer,
    ) -> None:
        self.base_renderer = base_renderer

    def render_png(
        self,
        status: RefugeCasinoStatus,
        visual: CasinoVisualState,
    ) -> bytes:
        context = _render_context(visual)
        world_state = _world_with_visual_overrides(status.state, visual)
        base_png = self.base_renderer.render_png(world_state, context=context)
        with Image.open(io.BytesIO(base_png)) as source:
            crop = source.convert("RGB").crop(CASINO_ROYAL_CROP_BOX)
            hero = crop.resize(CASINO_ROYAL_SIZE, Image.Resampling.NEAREST)
        hero = _apply_treatment(hero, visual)
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
