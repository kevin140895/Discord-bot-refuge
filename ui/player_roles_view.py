from __future__ import annotations

import discord

from config import (
    ROLE_ANTHYX_COMMUNITY,
    ROLE_CONSOLE,
    ROLE_MOBILE,
    ROLE_NOTIFICATION,
    ROLE_PARIS_SPORTIFS,
    ROLE_PC,
)
from view import PlayerTypeView, RoleView


PROFILE_ACCENT = discord.Colour(0x5865F2)


class _DelegateButton(discord.ui.Button):
    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        custom_id: str,
        callback,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._delegate_callback = callback

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._delegate_callback(interaction)


class PlayerTypePanelView(discord.ui.LayoutView):
    """Components V2 presentation for the existing PlayerTypeView contract."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._logic = PlayerTypeView()
        self.add_item(self._build_container())

    def _build_container(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=PROFILE_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🎮 QUEL TYPE DE JOUEUR ES-TU ?\n"
                "Choisis ta plateforme principale puis active les centres d'intérêt que tu souhaites afficher."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🕹️ Plateforme principale\n"
                "Un seul choix à la fois : sélectionner une nouvelle plateforme remplace l'ancienne."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                _DelegateButton(
                    label="💻 PC",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_pc",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_PC, "PC"),
                ),
                _DelegateButton(
                    label="🎮 Consoles",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_console",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_CONSOLE, "Consoles"),
                ),
                _DelegateButton(
                    label="📱 Mobile",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_mobile",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_MOBILE, "Mobile"),
                ),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 📌 Centres d'intérêt\n"
                "Ces badges sont indépendants de ta plateforme et peuvent être activés ou retirés à la carte."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                _DelegateButton(
                    label="🔔 Notifications",
                    style=discord.ButtonStyle.secondary,
                    custom_id="role_notifications",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_NOTIFICATION, "Notifications"),
                ),
                _DelegateButton(
                    label="👾 Anthyx Community",
                    style=discord.ButtonStyle.secondary,
                    custom_id="role_anthyx_community",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_ANTHYX_COMMUNITY, "Anthyx Community"),
                ),
                _DelegateButton(
                    label="🎯 Paris Sportifs",
                    style=discord.ButtonStyle.secondary,
                    custom_id="role_paris_sportifs",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_PARIS_SPORTIFS, "Paris Sportifs"),
                ),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Ta plateforme est exclusive · Tes centres d'intérêt restent indépendants."
            )
        )
        return container


class RolePanelView(discord.ui.LayoutView):
    """Components V2 presentation for the existing RoleView contract."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._logic = RoleView()
        self.add_item(self._build_container())

    def _build_container(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=PROFILE_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🆔 PERSONNALISE TON PROFIL JOUEUR\n"
                "Affiche tes badges sur ton profil pour que la communauté sache sur quoi tu joues et ce que tu aimes."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 🎮 Ta plateforme\n"
                "PC, Consoles ou Mobile — **choix unique**."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                _DelegateButton(
                    label="PC 💻",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_platform_pc",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_PC),
                ),
                _DelegateButton(
                    label="Consoles 🎮",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_platform_console",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_CONSOLE),
                ),
                _DelegateButton(
                    label="Mobile 📱",
                    style=discord.ButtonStyle.primary,
                    custom_id="role_platform_mobile",
                    callback=lambda i: self._logic._set_platform_role(i, ROLE_MOBILE),
                ),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 📌 Tes intérêts\n"
                "Notifications, communauté et paris sportifs — **à la carte**."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                _DelegateButton(
                    label="Notifications 🔔",
                    style=discord.ButtonStyle.success,
                    custom_id="role_interest_notifications",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_NOTIFICATION),
                ),
                _DelegateButton(
                    label="Anthyx Community 👾",
                    style=discord.ButtonStyle.secondary,
                    custom_id="role_interest_community",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_ANTHYX_COMMUNITY),
                ),
                _DelegateButton(
                    label="Paris Sportifs 🎯",
                    style=discord.ButtonStyle.secondary,
                    custom_id="role_interest_paris",
                    callback=lambda i: self._logic._toggle_role(i, ROLE_PARIS_SPORTIFS),
                ),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "### 🧹 Réinitialiser le profil\n"
                    "Retire en une seule action tous les rôles de plateforme et d'intérêt gérés par ce panneau."
                ),
                accessory=_DelegateButton(
                    label="Tout effacer 🗑️",
                    style=discord.ButtonStyle.danger,
                    custom_id="role_reset_all",
                    callback=self._logic._reset_roles,
                ),
            )
        )
        return container
