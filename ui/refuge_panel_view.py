from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal

import discord

from config import PARI_XP_CHANNEL_ID
from services.refuge_construction import refuge_construction_service
from services.refuge_exploration_runtime import refuge_exploration_runtime_service
from services.refuge_panel import RefugePanelSnapshot, refuge_panel_service
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
RefugePanelAction = Literal[
    "explore",
    "footprint",
    "timeline",
    "construction",
    "casino",
]
RefugeActivityKey = Literal["endormi", "calme", "vivant", "effervescent"]


@dataclass(frozen=True, slots=True)
class RefugeActivityPresentation:
    """Compact presentation of the current real-time Refuge activity."""

    key: RefugeActivityKey
    emoji: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class RefugeLiveStatus:
    """Small Discord-runtime read model displayed on the public Refuge panel."""

    day_number: int | None
    member_count: int
    voice_count: int
    radio_status: str
    ambience: str

    @property
    def signature(self) -> str:
        """Return visible runtime values used to avoid unnecessary panel edits."""

        day = self.day_number if self.day_number is not None else 0
        return (
            f"day={day}|members={self.member_count}|voice={self.voice_count}|"
            f"radio={self.radio_status}"
        )


def refuge_activity_presentation(voice_count: int) -> RefugeActivityPresentation:
    """Map cached human voice presence to one understandable Refuge state.

    The thresholds are intentionally small and deterministic for the current
    community size. They use only information already present in discord.py's
    guild/voice cache, so rendering the public panel never adds a REST request.
    """

    count = max(0, int(voice_count))
    if count == 0:
        return RefugeActivityPresentation(
            key="endormi",
            emoji="🌙",
            label="Refuge endormi",
            description="Le camp est silencieux, les braises veillent encore.",
        )
    if count == 1:
        return RefugeActivityPresentation(
            key="calme",
            emoji="🌿",
            label="Refuge calme",
            description="Quelques habitants veillent encore autour du feu.",
        )
    if count < 6:
        return RefugeActivityPresentation(
            key="vivant",
            emoji="🔥",
            label="Refuge vivant",
            description="Le feu crépite tandis que les habitants se rassemblent.",
        )
    return RefugeActivityPresentation(
        key="effervescent",
        emoji="⚡",
        label="Refuge effervescent",
        description="Le camp résonne de voix jusque dans les cabanes.",
    )


class RefugeCasinoPortalView(discord.ui.LayoutView):
    """Private bridge from the Refuge hub to the existing Casino channel."""

    def __init__(
        self,
        snapshot: RefugePanelSnapshot,
        *,
        guild_id: int | None,
    ) -> None:
        super().__init__(timeout=180)
        container = discord.ui.Container(accent_colour=discord.Colour.gold())
        casino_state = "Ouvert" if snapshot.casino_is_open else "Fermé"
        lines = [
            "## 🎰 Casino du Refuge",
            (
                f"**{snapshot.casino_name} · niveau {_roman(snapshot.casino_level)}** "
                f"— {snapshot.casino_fortune_name} · {casino_state}"
            ),
        ]
        if snapshot.casino_is_open and snapshot.casino_reaction.is_notable:
            lines.append(f"🎭 Ambiance : **{snapshot.casino_reaction.label}**")
        lines.append(
            "📜 Légendes : "
            f"**{snapshot.casino_public_legend_count}/{snapshot.casino_public_legend_total}** "
            "· 🔐 Mystères : "
            f"**{snapshot.casino_secret_legend_count}/{snapshot.casino_secret_legend_total}**"
        )
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        if guild_id is not None and PARI_XP_CHANNEL_ID > 0:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.ActionRow(
                    discord.ui.Button(
                        label="Entrer au Casino",
                        emoji="🎰",
                        style=discord.ButtonStyle.link,
                        url=(
                            f"https://discord.com/channels/{int(guild_id)}/"
                            f"{PARI_XP_CHANNEL_ID}"
                        ),
                    )
                )
            )
        self.add_item(container)


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
            elif self.action == "casino":
                snapshot = await refuge_panel_service.evaluate()
                view = RefugeCasinoPortalView(
                    snapshot,
                    guild_id=interaction.guild_id,
                )
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
        RefugePanelButton(
            action="casino",
            label="Casino",
            emoji="🎰",
            style=discord.ButtonStyle.secondary,
        ),
    )


class RefugePublicControlsView(discord.ui.LayoutView):
    """Callback-only persistent registration used across bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(refuge_controls_row())


class RefugePublicPanelView(discord.ui.LayoutView):
    """Permanent public Components V2 panel for the living Refuge."""

    def __init__(
        self,
        snapshot: RefugePanelSnapshot,
        *,
        live_status: RefugeLiveStatus | None = None,
    ) -> None:
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

        if live_status is not None:
            day_label = (
                str(live_status.day_number)
                if live_status.day_number is not None
                else "—"
            )
            activity = refuge_activity_presentation(live_status.voice_count)
            container.add_item(
                discord.ui.TextDisplay(
                    "### Vie du Refuge\n"
                    f"📅 **Jour {day_label}** · "
                    f"👥 **{live_status.member_count} habitants** · "
                    f"🎙️ **{live_status.voice_count} au feu de camp**\n"
                    f"📻 **Radio : {live_status.radio_status}**\n"
                    f"{activity.emoji} **{activity.label}** · "
                    f"*{activity.description}*"
                )
            )
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
        ]
        if snapshot.casino_is_open and snapshot.casino_reaction.is_notable:
            summary_lines.append(
                f"🎭 Casino : **{snapshot.casino_reaction.label}**"
            )
        summary_lines.append(
            "📜 Casino : "
            f"**{snapshot.casino_public_legend_count}/{snapshot.casino_public_legend_total} légendes** "
            "· 🔐 "
            f"**{snapshot.casino_secret_legend_count}/{snapshot.casino_secret_legend_total} mystères**"
        )
        summary_lines.append(f"🏗️ **{snapshot.construction_label}**")
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
    "RefugeActivityPresentation",
    "RefugeCasinoPortalView",
    "RefugeLiveStatus",
    "RefugePanelButton",
    "RefugePublicControlsView",
    "RefugePublicPanelView",
    "refuge_activity_presentation",
    "refuge_controls_row",
]
