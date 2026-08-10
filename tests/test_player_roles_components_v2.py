from unittest.mock import AsyncMock

import discord
import pytest

from config import (
    ROLE_ANTHYX_COMMUNITY,
    ROLE_CONSOLE,
    ROLE_MOBILE,
    ROLE_NOTIFICATION,
    ROLE_PARIS_SPORTIFS,
    ROLE_PC,
)
from ui.player_roles_view import PlayerTypePanelView, RolePanelView


def _buttons(view: discord.ui.LayoutView) -> dict[str, discord.ui.Button]:
    return {
        item.custom_id: item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button) and item.custom_id
    }


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_player_type_panel_is_components_v2_with_unchanged_buttons() -> None:
    view = PlayerTypePanelView()
    buttons = _buttons(view)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.is_persistent()
    assert "## 🎮 QUEL TYPE DE JOUEUR ES-TU ?" in _text(view)
    assert set(buttons) == {
        "role_pc",
        "role_console",
        "role_mobile",
        "role_notifications",
        "role_anthyx_community",
        "role_paris_sportifs",
    }
    assert {key: button.label for key, button in buttons.items()} == {
        "role_pc": "💻 PC",
        "role_console": "🎮 Consoles",
        "role_mobile": "📱 Mobile",
        "role_notifications": "🔔 Notifications",
        "role_anthyx_community": "👾 Anthyx Community",
        "role_paris_sportifs": "🎯 Paris Sportifs",
    }


def test_role_panel_is_components_v2_with_unchanged_buttons() -> None:
    view = RolePanelView()
    buttons = _buttons(view)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.is_persistent()
    assert "## 🆔 PERSONNALISE TON PROFIL JOUEUR" in _text(view)
    assert set(buttons) == {
        "role_platform_pc",
        "role_platform_console",
        "role_platform_mobile",
        "role_interest_notifications",
        "role_interest_community",
        "role_interest_paris",
        "role_reset_all",
    }
    assert {key: button.label for key, button in buttons.items()} == {
        "role_platform_pc": "PC 💻",
        "role_platform_console": "Consoles 🎮",
        "role_platform_mobile": "Mobile 📱",
        "role_interest_notifications": "Notifications 🔔",
        "role_interest_community": "Anthyx Community 👾",
        "role_interest_paris": "Paris Sportifs 🎯",
        "role_reset_all": "Tout effacer 🗑️",
    }


@pytest.mark.asyncio
async def test_player_type_panel_delegates_to_existing_role_logic() -> None:
    view = PlayerTypePanelView()
    buttons = _buttons(view)
    interaction = object()

    view._logic._set_platform_role = AsyncMock()
    view._logic._toggle_role = AsyncMock()

    await buttons["role_pc"].callback(interaction)
    view._logic._set_platform_role.assert_awaited_once_with(interaction, ROLE_PC, "PC")

    view._logic._set_platform_role.reset_mock()
    await buttons["role_console"].callback(interaction)
    view._logic._set_platform_role.assert_awaited_once_with(
        interaction, ROLE_CONSOLE, "Consoles"
    )

    view._logic._set_platform_role.reset_mock()
    await buttons["role_mobile"].callback(interaction)
    view._logic._set_platform_role.assert_awaited_once_with(
        interaction, ROLE_MOBILE, "Mobile"
    )

    await buttons["role_notifications"].callback(interaction)
    await buttons["role_anthyx_community"].callback(interaction)
    await buttons["role_paris_sportifs"].callback(interaction)
    assert view._logic._toggle_role.await_args_list == [
        ((interaction, ROLE_NOTIFICATION, "Notifications"), {}),
        ((interaction, ROLE_ANTHYX_COMMUNITY, "Anthyx Community"), {}),
        ((interaction, ROLE_PARIS_SPORTIFS, "Paris Sportifs"), {}),
    ]


@pytest.mark.asyncio
async def test_role_panel_delegates_to_existing_role_logic() -> None:
    view = RolePanelView()
    buttons = _buttons(view)
    interaction = object()

    view._logic._set_platform_role = AsyncMock()
    view._logic._toggle_role = AsyncMock()
    view._logic._reset_roles = AsyncMock()

    await buttons["role_platform_pc"].callback(interaction)
    await buttons["role_platform_console"].callback(interaction)
    await buttons["role_platform_mobile"].callback(interaction)
    assert view._logic._set_platform_role.await_args_list == [
        ((interaction, ROLE_PC), {}),
        ((interaction, ROLE_CONSOLE), {}),
        ((interaction, ROLE_MOBILE), {}),
    ]

    await buttons["role_interest_notifications"].callback(interaction)
    await buttons["role_interest_community"].callback(interaction)
    await buttons["role_interest_paris"].callback(interaction)
    assert view._logic._toggle_role.await_args_list == [
        ((interaction, ROLE_NOTIFICATION), {}),
        ((interaction, ROLE_ANTHYX_COMMUNITY), {}),
        ((interaction, ROLE_PARIS_SPORTIFS), {}),
    ]

    await buttons["role_reset_all"].callback(interaction)
    view._logic._reset_roles.assert_awaited_once_with(interaction)
