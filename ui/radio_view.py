from __future__ import annotations

import discord
from discord import app_commands


RADIO_ACCENT = discord.Colour(0x8B5CF6)


class RadioStationButton(discord.ui.Button):
    """Persistent station button used by the Components V2 radio panel."""

    def __init__(
        self,
        *,
        label: str,
        emoji: str,
        custom_id: str,
        command_name: str,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=custom_id,
        )
        self.command_name = command_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, RadioView):
            return
        await view.dispatch_station(interaction, self.command_name)


class RadioView(discord.ui.LayoutView):
    """Panneau radio persistant basé sur Discord Components V2."""

    STATIONS = (
        {
            "label": "Rap FR",
            "emoji": "🟣",
            "custom_id": "radio_rap_fr",
            "command_name": "radio_rap_fr",
            "description": "Rap francophone · sélection instantanée de la station.",
        },
        {
            "label": "Rap US",
            "emoji": "🔘",
            "custom_id": "radio_rap",
            "command_name": "radio_rap",
            "description": "Rap US · bascule le flux vocal vers la station américaine.",
        },
        {
            "label": "Rock",
            "emoji": "☢️",
            "custom_id": "radio_rock",
            "command_name": "radio_rock",
            "description": "Rock · une ambiance plus électrique dans le vocal du Refuge.",
        },
        {
            "label": "Radio Hip-Hop",
            "emoji": "📻",
            "custom_id": "radio_hiphop",
            "command_name": "radio_hiphop",
            "description": "Hip-Hop · la station principale de la radio du Refuge.",
        },
    )

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(self._build_container())

    def _build_container(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=RADIO_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 📻 RADIO DU REFUGE\n"
                "Choisis ton ambiance : la station sélectionnée prend immédiatement "
                "le relais dans le salon vocal Radio."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🎚️ Stations disponibles\n"
                "Chaque station possède son propre contrôle pour garder le panneau "
                "clair sur ordinateur comme sur mobile."
            )
        )

        for station in self.STATIONS:
            container.add_item(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        f"### {station['emoji']} {station['label']}\n"
                        f"{station['description']}"
                    ),
                    accessory=RadioStationButton(
                        label=station["label"],
                        emoji=station["emoji"],
                        custom_id=station["custom_id"],
                        command_name=station["command_name"],
                    ),
                )
            )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🔁 Changement rapide\n"
                "Rap FR, Rap US et Rock conservent le comportement existant : "
                "si tu rappuies sur la station déjà active et qu'une station précédente "
                "est disponible, le bot y revient."
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                "-# La Radio Hip-Hop reste la station principale · Aucun réglage audio n'est modifié par ce panneau."
            )
        )
        return container

    async def dispatch_station(
        self,
        interaction: discord.Interaction,
        command_name: str,
    ) -> None:
        """Dispatch to the existing RadioCog methods without changing radio logic."""
        cog = interaction.client.get_cog("RadioCog")
        if not cog:
            await interaction.response.send_message(
                "❌ Radio indisponible.", ephemeral=True
            )
            return

        command = getattr(cog, command_name, None)
        if command is None:
            await interaction.response.send_message(
                "❌ Radio indisponible.", ephemeral=True
            )
            return

        if isinstance(command, app_commands.Command):
            await command.callback(cog, interaction)
        else:
            await command(interaction)
