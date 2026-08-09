from __future__ import annotations

import logging
from typing import Final, Literal

import discord

from services.refuge_construction import refuge_construction_service
from services.refuge_exploration_runtime import refuge_exploration_runtime_service
from services.refuge_panel import RefugePanelSnapshot
from services.refuge_timeline import refuge_timeline_service
from ui.refuge_construction_view import RefugeConstructionView
from ui.refuge_exploration_view import (
    RefugeExplorerView,
    RefugeFootprintView,
    RefugePrivateErrorView,
)
from ui.refuge_timeline_view import RefugeTimelineView


logger = logging.getLogger(__name__)
REFUGE_PANEL_ACCENT = discord.Colour(0xD08A47)
REFUGE_MAP_FILENAME: Final[str] = "refuge-map.png"
RefugePanelAction = Literal["explore", "footprint", "timeline", "construction"]


class RefugePanelButton(discord.ui.Button):
    """Persistent public control dispatching to private Refuge surfaces."""

    def __init__(
        self,
        *,
        action: RefugePanelAction,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"refuge:panel:{action}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        timeline_file: discord.File | None = None
        try:
            if self.action == "explore":
                snapshot = await refuge_exploration_runtime_service.get_explorer()
                view: discord.ui.LayoutView = RefugeExplorerView(
                    snapshot,
                    owner_user_id=interaction.user.id,
                )
            elif self.action == "footprint":
                snapshot = await refuge_exploration_runtime_service.get_footprint(
                    interaction.user.id
                )
                avatar = getattr(interaction.user, "display_avatar", None)
                avatar_url = (
                    str(avatar.url)
                    if getattr(avatar, "url", None)
                    else None
                )
                display_name = str(
                    getattr(
                        interaction.user,
                        "display_name",
                        getattr(interaction.user, "name", interaction.user.id),
                    )
                )
                view = RefugeFootprintView(
                    snapshot,
                    display_name=display_name,
                    avatar_url=avatar_url,
                )
            elif self.action == "timeline":
                snapshot = await refuge_timeline_service.get_timeline()
                timeline_view = RefugeTimelineView(
                    snapshot,
                    owner_user_id=interaction.user.id,
                )
                timeline_file = await timeline_view.selected_file()
                view = timeline_view
            else:
                await refuge_timeline_service.sync()
                snapshot = await refuge_construction_service.get_snapshot(
                    interaction.user.id
                )
                view = RefugeConstructionView(
                    snapshot,
                    owner_user_id=interaction.user.id,
                )
        except Exception:
            logger.exception(
                "[refuge] impossible de charger l'action privée %s pour user=%s",
                self.action,
                interaction.user.id,
            )
            view = RefugePrivateErrorView(
                "Impossible de charger cette partie du Refuge pour le moment."
            )
            timeline_file = None

        if self.action == "timeline":
            await interaction.edit_original_response(
                view=view,
                attachments=[timeline_file] if timeline_file is not None else [],
            )
        else:
            await interaction.edit_original_response(view=view)


def refuge_controls_row() -> discord.ui.ActionRow:
    return discord.ui.ActionRow(
        RefugePanelButton(
            action="explore",
            label="Explorer",
            emoji="🔎",
            style=discord.ButtonStyle.primary,
        ),
        RefugePanelButton(
            action="footprint",
            label="Mon empreinte",
            emoji="👤",
            style=discord.ButtonStyle.secondary,
        ),
        RefugePanelButton(
            action="timeline",
            label="Chronologie",
            emoji="🕰️",
            style=discord.ButtonStyle.secondary,
        ),
        RefugePanelButton(
            action="construction",
            label="Chantier",
            emoji="🏗️",
            style=discord.ButtonStyle.success,
        ),
    )


class RefugePublicControlsView(discord.ui.LayoutView):
    """Callback-only persistent registration used across bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(refuge_controls_row())


class RefugePublicPanelView(discord.ui.LayoutView):
    """Permanent public Components V2 panel for the living Refuge."""

    def __init__(self, snapshot: RefugePanelSnapshot) -> None:
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_colour=REFUGE_PANEL_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                f"## 🏕️ LE REFUGE — {snapshot.season_label}\n"
                "Un monde façonné par l’activité réelle de la communauté."
            )
        )

        gallery = discord.ui.MediaGallery()
        gallery.add_item(
            media=f"attachment://{REFUGE_MAP_FILENAME}",
            description=f"Carte actuelle du Refuge — {snapshot.season_label}",
        )
        container.add_item(gallery)
        container.add_item(discord.ui.Separator())

        casino_state = "Ouvert" if snapshot.casino_is_open else "Fermé"
        summary_lines = [
            "### État du Refuge",
            (
                f"🔥 **{snapshot.fire_name} · niveau {_roman(snapshot.fire_level)}** "
                f"— {snapshot.fire_intensity_name}"
            ),
            f"🏆 **{snapshot.hall_name} · niveau {_roman(snapshot.hall_level)}**",
            (
                f"🎰 **{snapshot.casino_name} · niveau {_roman(snapshot.casino_level)}** "
                f"— {snapshot.casino_fortune_name} · {casino_state}"
            ),
            f"🏗️ **{snapshot.construction_label}**",
        ]
        if snapshot.latest_event_label:
            summary_lines.append(f"🌌 Dernière trace : **{snapshot.latest_event_label}**")
        container.add_item(discord.ui.TextDisplay("\n".join(summary_lines)))
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Le Refuge ne se réinitialise pas : ses évolutions deviennent son histoire."
            )
        )
        container.add_item(refuge_controls_row())
        self.add_item(container)


def _roman(level: int) -> str:
    values = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
    return values.get(max(1, min(5, int(level))), str(int(level)))


__all__ = [
    "REFUGE_MAP_FILENAME",
    "REFUGE_PANEL_ACCENT",
    "RefugePanelButton",
    "RefugePublicControlsView",
    "RefugePublicPanelView",
    "refuge_controls_row",
]
