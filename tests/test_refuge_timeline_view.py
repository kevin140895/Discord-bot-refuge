from __future__ import annotations

from datetime import datetime, timezone

import discord
import pytest

from models.refuge_world import RefugeWorldState
from rendering.refuge_world import RefugeRenderContext
from services.refuge_timeline import RefugeTimelineChapter, RefugeTimelineSnapshot
from ui.refuge_timeline_view import (
    REFUGE_HISTORY_FILENAME,
    RefugeTimelinePageButton,
    RefugeTimelineSelect,
    RefugeTimelineView,
)


def _chapter(season_id: str, label: str) -> RefugeTimelineChapter:
    return RefugeTimelineChapter(
        season_id=season_id,
        label=label,
        captured_at="2026-09-01T00:00:00+00:00",
        state=RefugeWorldState(),
        context=RefugeRenderContext(season="summer", daypart="night"),
        chapter_event_count=2,
        chapter_event_labels=("Un monument a été inauguré", "Une nouvelle trace est entrée au Hall"),
        building_count=3,
        monument_count=1,
        fire_level=3,
        hall_level=2,
        casino_level=1,
        construction_label="Observatoire des Étoiles",
    )


def _text(view: RefugeTimelineView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class _Service:
    def __init__(self) -> None:
        self.calls = []

    async def render_chapter_png(self, chapter):
        self.calls.append(chapter.season_id)
        return b"PNG"


def test_timeline_without_archive_explains_first_month() -> None:
    snapshot = RefugeTimelineSnapshot(
        current_season_id="2026-08",
        current_season_label="Août 2026",
        chapters=(),
        selected_season_id=None,
    )
    view = RefugeTimelineView(snapshot, owner_user_id=42)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout == 300
    text = _text(view)
    assert "CHRONOLOGIE DU REFUGE" in text
    assert "Août 2026" in text
    assert "Aucun chapitre mensuel n’est clôturé" in text
    assert not any(isinstance(item, discord.ui.MediaGallery) for item in view.walk_children())


def test_timeline_chapter_renders_archived_summary_and_attachment() -> None:
    chapter = _chapter("2026-08", "Août 2026")
    snapshot = RefugeTimelineSnapshot(
        current_season_id="2026-09",
        current_season_label="Septembre 2026",
        chapters=(chapter,),
        selected_season_id="2026-08",
    )
    view = RefugeTimelineView(snapshot, owner_user_id=42)

    text = _text(view)
    assert "Août 2026" in text
    assert "Feu : **niveau III**" in text
    assert "Hall : **niveau II**" in text
    assert "Casino : **niveau I**" in text
    assert "Monuments permanents : **1**" in text
    assert "Événements du chapitre : **2**" in text
    assert "Observatoire des Étoiles" in text
    assert "sans recalcul de progression" in text

    galleries = [
        item for item in view.walk_children() if isinstance(item, discord.ui.MediaGallery)
    ]
    assert len(galleries) == 1
    assert galleries[0].items[0].media.url == f"attachment://{REFUGE_HISTORY_FILENAME}"
    selects = [item for item in view.walk_children() if isinstance(item, RefugeTimelineSelect)]
    assert len(selects) == 1
    assert selects[0].options[0].default is True


@pytest.mark.asyncio
async def test_selected_file_uses_archived_chapter_renderer() -> None:
    chapter = _chapter("2026-08", "Août 2026")
    snapshot = RefugeTimelineSnapshot(
        current_season_id="2026-09",
        current_season_label="Septembre 2026",
        chapters=(chapter,),
        selected_season_id="2026-08",
    )
    service = _Service()
    view = RefugeTimelineView(snapshot, owner_user_id=42, service=service)

    file = await view.selected_file()

    assert file is not None
    assert file.filename == REFUGE_HISTORY_FILENAME
    assert service.calls == ["2026-08"]


def test_timeline_paginates_more_than_25_months() -> None:
    chapters = tuple(
        _chapter(f"2026-{month:02d}", f"Chapitre {index}")
        for index, month in enumerate(range(1, 13), start=1)
    ) + tuple(
        _chapter(f"2025-{month:02d}", f"Ancien {index}")
        for index, month in enumerate(range(1, 13), start=1)
    ) + (
        _chapter("2024-12", "Décembre 2024"),
        _chapter("2024-11", "Novembre 2024"),
    )
    snapshot = RefugeTimelineSnapshot(
        current_season_id="2026-09",
        current_season_label="Septembre 2026",
        chapters=chapters,
        selected_season_id=chapters[0].season_id,
    )
    view = RefugeTimelineView(snapshot, owner_user_id=42)

    assert view.page_count == 2
    assert len(view.page_chapters) == 25
    buttons = [
        item for item in view.walk_children() if isinstance(item, RefugeTimelinePageButton)
    ]
    assert len(buttons) == 2
    assert any(button.label == "Plus ancien" and not button.disabled for button in buttons)

    view.change_page(1)
    assert view.page_index == 1
    assert len(view.page_chapters) == 1
    assert view.selected_season_id == chapters[25].season_id
