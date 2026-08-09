from __future__ import annotations

from datetime import datetime, timezone

import discord

from services.refuge_exploration import (
    EXPLORER_ZONE_ORDER,
    RefugeExplorerSnapshot,
    RefugeFootprintSnapshot,
)
from utils.timezones import PARIS_TZ


REFUGE_EXPLORATION_ACCENT = discord.Colour(0xD08A47)


def _format_number(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _format_signed_xp(value: int) -> str:
    return f"{int(value):+d} XP"


def _format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} h {minutes:02d}"
    return f"{minutes} min"


def _format_trace_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "Date inconnue"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(PARIS_TZ).strftime("%d/%m/%Y")


class RefugeExplorerSelect(discord.ui.Select):
    def __init__(
        self,
        snapshot: RefugeExplorerSnapshot,
        *,
        selected_zone_id: str,
    ) -> None:
        options = [
            discord.SelectOption(
                label=zone.title,
                value=zone.zone_id,
                emoji=zone.emoji,
                default=zone.zone_id == selected_zone_id,
            )
            for zone in snapshot.zones
        ]
        super().__init__(
            custom_id="refuge:explorer:zone",
            placeholder="Choisir un lieu du Refuge",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, RefugeExplorerView):
            return
        view.show_zone(self.values[0])
        await interaction.response.edit_message(view=view)


class RefugeExplorerView(discord.ui.LayoutView):
    """Private V2 navigator over current Refuge places and discovered history."""

    def __init__(
        self,
        snapshot: RefugeExplorerSnapshot,
        *,
        owner_user_id: int,
        initial_zone_id: str = "fire",
    ) -> None:
        super().__init__(timeout=300)
        self.snapshot = snapshot
        self.owner_user_id = int(owner_user_id)
        self.selected_zone_id = (
            initial_zone_id if initial_zone_id in EXPLORER_ZONE_ORDER else "fire"
        )
        self.show_zone(self.selected_zone_id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message(
            "Cette exploration privée appartient à la personne qui l’a ouverte.",
            ephemeral=True,
        )
        return False

    def show_zone(self, zone_id: str) -> None:
        zone = self.snapshot.get_zone(zone_id)
        self.selected_zone_id = zone.zone_id
        self.clear_items()

        container = discord.ui.Container(accent_colour=REFUGE_EXPLORATION_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🔎 EXPLORER LE REFUGE\n"
                "Observe les lieux tels qu’ils existent réellement aujourd’hui."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"### {zone.emoji} {zone.title}\n"
                + "\n".join(f"• {line}" for line in zone.details)
            )
        )

        if zone.history:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "### Traces récentes\n"
                    + "\n".join(f"• {line}" for line in zone.history)
                )
            )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.ActionRow(
                RefugeExplorerSelect(
                    self.snapshot,
                    selected_zone_id=self.selected_zone_id,
                )
            )
        )
        if zone.zone_id == "mysteries":
            container.add_item(
                discord.ui.TextDisplay(
                    "-# Les mystères non découverts et leurs conditions restent invisibles."
                )
            )
        self.add_item(container)


class RefugeFootprintView(discord.ui.LayoutView):
    """Private, non-ranked representation of one member's Refuge footprint."""

    def __init__(
        self,
        snapshot: RefugeFootprintSnapshot,
        *,
        display_name: str,
        avatar_url: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        container = discord.ui.Container(accent_colour=REFUGE_EXPLORATION_ACCENT)

        identity = discord.ui.TextDisplay(
            "## 👤 MON EMPREINTE\n"
            f"**{display_name}**\n"
            "-# Ce que ton activité a réellement laissé dans le Refuge."
        )
        if avatar_url:
            container.add_item(
                discord.ui.Section(
                    identity,
                    accessory=discord.ui.Thumbnail(
                        avatar_url,
                        description=f"Avatar de {display_name}",
                    ),
                )
            )
        else:
            container.add_item(identity)

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"### 📅 {snapshot.season_label}\n"
                f"⚡ XP gagnée : **{_format_number(snapshot.season_xp)} XP**\n"
                f"💬 Messages : **{_format_number(snapshot.season_messages)}**\n"
                f"🎧 Vocal : **{_format_duration(snapshot.season_voice_seconds)}**\n"
                f"🎰 Casino net : **{_format_signed_xp(snapshot.season_casino_net)}**"
            )
        )

        container.add_item(discord.ui.Separator())
        permanent_lines = [
            "### 🏕️ Traces permanentes",
            (
                f"Progression actuelle : **niveau {snapshot.level}** · "
                f"**{_format_number(snapshot.xp)} XP**"
            ),
            (
                f"Succès débloqués : **{snapshot.achievements_unlocked}/"
                f"{snapshot.achievements_total}**"
            ),
            (
                f"Casino historique : **{_format_number(snapshot.casino_bets)} paris** · "
                f"**{_format_signed_xp(snapshot.casino_net)}**"
            ),
        ]
        if snapshot.achievement_names:
            permanent_lines.append(
                "Badges : " + " · ".join(snapshot.achievement_names)
            )
        else:
            permanent_lines.append("Badges : aucun succès débloqué pour le moment.")
        container.add_item(discord.ui.TextDisplay("\n".join(permanent_lines)))

        container.add_item(discord.ui.Separator())
        if snapshot.historical_traces:
            trace_lines = ["### 📜 Dans l’histoire du Refuge"]
            trace_lines.extend(
                f"• **{_format_trace_date(trace.occurred_at)}** · {trace.label}"
                for trace in snapshot.historical_traces
            )
        else:
            trace_lines = [
                "### 📜 Dans l’histoire du Refuge",
                "Aucune trace nominative permanente n’est encore enregistrée pour toi.",
            ]
        container.add_item(discord.ui.TextDisplay("\n".join(trace_lines)))
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Ton empreinte n’est pas un classement : aucune comparaison d’importance entre membres n’est calculée."
            )
        )
        self.add_item(container)


class RefugePrivateErrorView(discord.ui.LayoutView):
    def __init__(self, message: str) -> None:
        super().__init__(timeout=60)
        container = discord.ui.Container(accent_colour=discord.Colour.red())
        container.add_item(discord.ui.TextDisplay("## 🏕️ Le Refuge"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(str(message)))
        self.add_item(container)


__all__ = [
    "REFUGE_EXPLORATION_ACCENT",
    "RefugeExplorerSelect",
    "RefugeExplorerView",
    "RefugeFootprintView",
    "RefugePrivateErrorView",
]
