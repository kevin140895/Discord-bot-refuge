"""Deterministic visual rendering helpers for the Refuge world."""

from .refuge_fire import (
    REFUGE_FIRE_RENDERER_VERSION,
    RefugeFireRenderer,
    fire_scene_signature,
    refuge_fire_renderer,
)
from .refuge_hall import (
    REFUGE_HALL_RENDERER_VERSION,
    RefugeHallRenderer,
    hall_scene_signature,
    refuge_hall_renderer,
)
from .refuge_world import (
    REFUGE_CANVAS_SIZE,
    RefugeRenderContext,
    RefugeWorldRenderer,
    daypart_for_hour,
    refuge_world_renderer,
    scene_render_signature,
    season_for_month,
)

__all__ = [
    "REFUGE_CANVAS_SIZE",
    "REFUGE_FIRE_RENDERER_VERSION",
    "REFUGE_HALL_RENDERER_VERSION",
    "RefugeFireRenderer",
    "RefugeHallRenderer",
    "RefugeRenderContext",
    "RefugeWorldRenderer",
    "daypart_for_hour",
    "fire_scene_signature",
    "hall_scene_signature",
    "refuge_fire_renderer",
    "refuge_hall_renderer",
    "refuge_world_renderer",
    "scene_render_signature",
    "season_for_month",
]
