from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Final

import discord

from services.refuge_timeline import (
    RefugeTimelineChapter,
    RefugeTimelineService,
    RefugeTimelineSnapshot,
    refuge_timeline_service,
)
from utils.timezones import PARIS_TZ


REFUGE_TIMELINE_ACCENT = discord.Colour(0xD08A47)
REFUGE_HISTORY_FILENAME: Final[str] = "refuge-history.png"
_TIMELINE_PAGE_SIZE: Final[int] = 25


def _format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "date inconnue"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(PARIS_TZ)
    return local.strftime("%d/%m/%Y à %H:%M")


def _roman(level: int) -> str:
    values = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
    normalized = max(0, int(level))
    return values.get(normalized, str(normalized))


class RefugeTimelineSelect(discord.ui.Select):
    def __init__(self, view: "RefugeTimelineView") -> None:
        page = view.page_chapters
        options = [
            discord.SelectOption(
                label=chapter.label,
                value=chapter.season_id,
                description=(
                    f"{chapter.chapter_event_count} événement(s) · "
                    f"{chapter.monument_count} monument(s)"
                )[:100],
                default=chapter.season_id == view.selected_season_id,
            )
            for chapter in page
        ]
        super().__init__(
            custom_id="refuge:timeline:season",
            placeholder="Choisir un chapitre",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, RefugeTimelineView):
            return
        await interaction.response.defer()
        view.select_season(self.values[0])
        await view.edit_interaction(interaction)


class RefugeTimelinePageButton(discord.ui.Button):
    def __init__(self, *, direction: int, disabled: bool) -> None:
        self.direction = -1 if direction < 0 else 1
        super().__init__(
            label="Plus récent" if self.direction < 0 else "Plus ancien",
            emoji="◀️" if self.direction < 0 else "▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "refuge:timeline:newer" if self.direction < 0 else "refuge:timeline:older"
            ),
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, RefugeTimelineView):
            return
        await interaction.response.defer()
        view.change_page(self.direction)
        await view.edit_interaction(interaction)


class RefugeTimelineView(discord.ui.LayoutView):
    """Private Components V2 browser over immutable monthly Refuge snapshots."""

    def __init__(
        self,
        snapshot: RefugeTimelineSnapshot,
        *,
        owner_user_id: int,
        service: RefugeTimelineService = refuge_timeline_service,
        page_index: int | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.snapshot = snapshot
        self.owner_user_id = int(owner_user_id)
        self.service = service
        self.selected_season_id = snapshot.selected_season_id
        selected_index = self._selected_index()
        self.page_index = (
            max(0, int(page_index))
            if page_index is not None
            else (selected_index // _TIMELINE_PAGE_SIZE if selected_index >= 0 else 0)
        )
        self._clamp_page()
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message(
            "Cette Chronologie privée appartient à la personne qui l’a ouverte.",
            ephemeral=True,
        )
        return False

    @property
    def page_count(self) -> int:
        if not self.snapshot.chapters:
            return 1
        return (len(self.snapshot.chapters) - 1) // _TIMELINE_PAGE_SIZE + 1

    @property
    def page_chapters(self) -> tuple[RefugeTimelineChapter, ...]:
        start = self.page_index * _TIMELINE_PAGE_SIZE
        end = start + _TIMELINE_PAGE_SIZE
        return self.snapshot.chapters[start:end]

    @property
    def selected(self) -> RefugeTimelineChapter | None:
        if self.selected_season_id is None:
            return None
        return next(
            (
                chapter
                for chapter in self.snapshot.chapters
                if chapter.season_id == self.selected_season_id
            ),
            None,
        )

    def _selected_index(self) -> int:
        if self.selected_season_id is None:
            return -1
        for index, chapter in enumerate(self.snapshot.chapters):
            if chapter.season_id == self.selected_season_id:
                return index
        return -1

    def _clamp_page(self) -> None:
        self.page_index = max(0, min(self.page_index, self.page_count - 1))

    def select_season(self, season_id: str) -> None:
        for index, chapter in enumerate(self.snapshot.chapters):
            if chapter.season_id != season_id:
                continue
            self.selected_season_id = season_id
            self.page_index = index // _TIMELINE_PAGE_SIZE
            self.rebuild()
            return

    def change_page(self, direction: int) -> None:
        self.page_index += -1 if direction < 0 else 1
        self._clamp_page()
        page = self.page_chapters
        if page:
            self.selected_season_id = page[0].season_id
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=REFUGE_TIMELINE_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🕰️ CHRONOLOGIE DU REFUGE\n"
                "Les saisons clôturées deviennent des chapitres permanents."
            )
        )
        container.add_item(discord.ui.Separator())

        chapter = self.selected
        if chapter is None:
            container.add_item(
                discord.ui.TextDisplay(
                    f"### 📖 {self.snapshot.current_season_label}\n"
                    "La première saison du Refuge est encore en cours.\n"
                    "Aucun chapitre mensuel n’est clôturé pour le moment."
                )
            )
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "-# Le premier snapshot sera figé au prochain changement de mois Europe/Paris."
                )
            )
            self.add_item(container)
            return

        gallery = discord.ui.MediaGallery()
        gallery.add_item(
            media=f"attachment://{REFUGE_HISTORY_FILENAME}",
            description=f"Carte historique du Refuge — {chapter.label}",
        )
        container.add_item(gallery)
        container.add_item(discord.ui.Separator())

        lines = [
            f"### 📖 {chapter.label}",
            f"🔥 Feu : **niveau {_roman(chapter.fire_level)}**",
            f"🏆 Hall : **niveau {_roman(chapter.hall_level)}**",
            f"🎰 Casino : **niveau {_roman(chapter.casino_level)}**",
            f"🏛️ Monuments permanents : **{chapter.monument_count}**",
            f"🌌 Événements du chapitre : **{chapter.chapter_event_count}**",
        ]
        if chapter.construction_label:
            lines.append(f"🏗️ Chantier à la clôture : **{chapter.construction_label}**")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        if chapter.chapter_event_labels:
            container.add_item(discord.ui.Separator())
            event_lines = ["### Traces du mois"]
            event_lines.extend(f"• {label}" for label in chapter.chapter_event_labels)
            container.add_item(discord.ui.TextDisplay("\n".join(event_lines)))

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"-# Snapshot figé le {_format_timestamp(chapter.captured_at)} · "
                "la carte est régénérée depuis cet état archivé, sans recalcul de progression."
            )
        )

        controls = discord.ui.ActionRow()
        if self.page_chapters:
            controls.add_item(RefugeTimelineSelect(self))
        container.add_item(controls)

        if self.page_count > 1:
            container.add_item(
                discord.ui.ActionRow(
                    RefugeTimelinePageButton(
                        direction=-1,
                        disabled=self.page_index <= 0,
                    ),
                    RefugeTimelinePageButton(
                        direction=1,
                        disabled=self.page_index >= self.page_count - 1,
                    ),
                )
            )
            container.add_item(
                discord.ui.TextDisplay(
                    f"-# Archives {self.page_index + 1}/{self.page_count} · "
                    "25 chapitres maximum par page."
                )
            )
        self.add_item(container)

    async def selected_file(self) -> discord.File | None:
        chapter = self.selected
        if chapter is None:
            return None
        png = await self.service.render_chapter_png(chapter)
        return discord.File(io.BytesIO(png), filename=REFUGE_HISTORY_FILENAME)

    async def edit_interaction(self, interaction: discord.Interaction) -> None:
        file = await self.selected_file()
        kwargs: dict[str, object] = {"view": self}
        kwargs["attachments"] = [file] if file is not None else []
        await interaction.edit_original_response(**kwargs)


__all__ = [
    "REFUGE_HISTORY_FILENAME",
    "REFUGE_TIMELINE_ACCENT",
    "RefugeTimelinePageButton",
    "RefugeTimelineSelect",
    "RefugeTimelineView",
]
