from __future__ import annotations

import io

from PIL import Image

from models.refuge_world import RefugeWorldState
from rendering.refuge_live_activity import (
    apply_refuge_activity_overlay,
    normalize_activity_key,
)
from rendering.refuge_world import RefugeRenderContext, RefugeWorldRenderer


def _base_png(*, daypart: str = "day", hour: int = 14) -> tuple[bytes, RefugeRenderContext]:
    context = RefugeRenderContext(
        season="summer",
        daypart=daypart,
        local_hour=hour,
    )
    png = RefugeWorldRenderer().render_png(RefugeWorldState(), context=context)
    return png, context


def test_activity_overlay_is_deterministic_and_preserves_png_shape() -> None:
    base, context = _base_png()

    first = apply_refuge_activity_overlay(
        base,
        activity_key="vivant",
        context=context,
    )
    second = apply_refuge_activity_overlay(
        base,
        activity_key="vivant",
        context=context,
    )

    assert first == second
    assert first != base
    with Image.open(io.BytesIO(first)) as image:
        assert image.size == (1280, 720)
        assert image.mode == "RGB"


def test_each_activity_bucket_changes_the_public_scene() -> None:
    base, context = _base_png(daypart="night", hour=23)

    rendered = {
        key: apply_refuge_activity_overlay(base, activity_key=key, context=context)
        for key in ("endormi", "calme", "vivant", "effervescent")
    }

    assert len(set(rendered.values())) == 4


def test_invalid_activity_key_falls_back_to_sleeping_state() -> None:
    base, context = _base_png()

    invalid = apply_refuge_activity_overlay(
        base,
        activity_key="unknown",
        context=context,
    )
    sleeping = apply_refuge_activity_overlay(
        base,
        activity_key="endormi",
        context=context,
    )

    assert normalize_activity_key("unknown") == "endormi"
    assert invalid == sleeping
