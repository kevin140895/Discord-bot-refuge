"""Fail-closed permission guard for economy administration commands.

This extension is deliberately named with a ``zz_`` prefix so the automatic
cog loader imports it after the regular command cogs and before the command
tree is synchronised. It replaces legacy role-based checks on economy-mutating
commands with an explicit ``manage_guild`` permission check.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.app_commands.errors import MissingPermissions, NoPrivateMessage
from discord.ext import commands


MAX_MANUAL_XP_GRANT = 10_000


def require_manage_guild(interaction: discord.Interaction) -> bool:
    """Require the Discord ``manage_guild`` permission for an interaction."""

    if interaction.guild is None:
        raise NoPrivateMessage()
    if not interaction.permissions.manage_guild:
        raise MissingPermissions(["manage_guild"])
    return True


def _require_command(command: object | None, qualified_name: str):
    if command is None or not hasattr(command, "checks"):
        raise RuntimeError(
            f"Economy admin guard could not resolve /{qualified_name}; refusing startup"
        )
    return command


def harden_economy_admin_commands(bot: commands.Bot) -> None:
    """Replace legacy role checks on economy mutations before tree sync.

    The source commands historically used ``XP_VIEWER_ROLE_ID``, which is also
    the level-10 Argent reward. Replacing the effective check list here keeps
    that community role usable for its non-economic features while ensuring it
    cannot grant XP or free machine tickets.
    """

    don_xp = _require_command(bot.tree.get_command("don_xp"), "don_xp")
    don_xp.checks[:] = [require_manage_guild]

    params = getattr(don_xp, "_params", {})
    amount_param = params.get("montant")
    if amount_param is None:
        raise RuntimeError(
            "Economy admin guard could not resolve /don_xp montant; refusing startup"
        )
    amount_param.max_value = MAX_MANUAL_XP_GRANT

    machine = bot.tree.get_command("machine")
    if machine is None or not hasattr(machine, "get_command"):
        raise RuntimeError(
            "Economy admin guard could not resolve /machine; refusing startup"
        )
    ticket = _require_command(machine.get_command("ticket"), "machine ticket")
    ticket.checks[:] = [require_manage_guild]


async def setup(bot: commands.Bot) -> None:
    harden_economy_admin_commands(bot)
