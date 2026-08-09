from __future__ import annotations

from typing import Final, Literal

import discord

from services.refuge_panel import RefugePanelSnapshot


REFUGE_PANEL_ACCENT = discord.Colour(0xD08A47)
REFUGE_MAP_FILENAME: Final[str] = "refuge-map.png"
RefugePanelAction = Literal["explore", "footprint", "timeline", "construction"]

_ACTION_COPY: Final[dict[str, tuple[str, str, str]]] = {
    "explore": (
        "🔎 Explorer",
        "L’exploration détaillée des lieux du Refuge sera activée dans la prochaine évolution.",
        "Le panneau public est déjà prêt à accueillir cette navigation.",
    ),
    "footprint": (
        "👤 Mon empreinte",
        "Ton empreinte personnelle sera consultable ici dans une vue privée.",
        "Elle ne sera jamais affichée publiquement sur le panneau du Refuge.",
    ),
    "timeline": (
        "🕰️ Chronologie",
        "Les archives mensuelles du Refuge seront consultables depuis ce bouton.",
        "Les saisons passées deviendront des chapitres permanents de son histoire.",
    ),
    "construction": (
        "🏗️ Chantier",
        "Les votes et le suivi des constructions apparaîtront ici lorsqu’un chantier sera ouvert.",
        "Aucune action artificielle ne sera nécessaire pour accélérer une construction.",
    ),
}


def _roman(level: int) -> str:
    values = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
    return values.get(max(1, min(5, int(level))), str(int(level)))


class RefugePendingActionView(discord.ui.LayoutView):
    """Small ephemeral V2 response used until REFUGE-009/010/011 land."""

    def __init__(self, action: RefugePanelAction) -> None:
        super().__init__(timeout=120)
        title, primary, secondary = _ACTION_COPY[action]
        container = discord.ui.Container(accent_colour=REFUGE_PANEL_ACCENT)
        container.add_item(discord.ui.TextDisplay(f"## {title}"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"{primary}\n\n-# {secondary}"))
        self.add_item(container)


class RefugePanelButton(discord.ui.Button):
    """Persistent public control; detailed action content lands in later stages."""

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
        await interaction.response.send_message(
            view=RefugePendingActionView(self.action),
            ephemeral=True,
        )


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


__all__ = [
    "REFUGE_MAP_FILENAME",
    "REFUGE_PANEL_ACCENT",
    "RefugePanelButton",
    "RefugePendingActionView",
    "RefugePublicControlsView",
    "RefugePublicPanelView",
    "refuge_controls_row",
]
