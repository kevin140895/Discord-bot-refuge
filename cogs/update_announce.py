"""Publication d'annonces de mise à jour via une commande slash."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from config import DATA_DIR, UPDATE_CHANNEL_ID
from utils.interactions import safe_respond
from utils.persistence import (
    atomic_write_json_async,
    ensure_dir,
    read_json_safe,
)

logger = logging.getLogger(__name__)


VERSION_FILE = os.path.join(DATA_DIR, "version.json")

MONTHS_FR = [
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
]


def _format_date_fr(dt: datetime) -> str:
    return f"{dt.day:02d} {MONTHS_FR[dt.month - 1]} {dt.year}"


def _normalize_lines(raw: str) -> tuple[str, list[str]]:
    """Retourne (description, lignes normalisées)."""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    description = ""
    if lines and lines[0].startswith(">"):
        description = lines.pop(0)[1:].strip()
    normalized: list[str] = []
    for line in lines:
        if line.startswith("-"):
            line = "•" + line[1:]
        elif not line.startswith("•"):
            line = "• " + line
        normalized.append(line)
    return description, normalized


async def _bump_version(category: str | None) -> str:
    """Lit, incrémente et enregistre la version."""

    ensure_dir(DATA_DIR)
    data = read_json_safe(VERSION_FILE)
    current = data.get("current", "1.0.0")
    try:
        major, minor, patch = [int(x) for x in current.split(".")]
    except Exception:
        major, minor, patch = 1, 0, 0
    if category == "feature":
        minor += 1
        patch = 0
    else:
        patch += 1
    new_version = f"{major}.{minor}.{patch}"
    await atomic_write_json_async(VERSION_FILE, {"current": new_version})
    return new_version


class UpdateModal(discord.ui.Modal):
    def __init__(self, cog: "UpdateAnnounceCog") -> None:
        super().__init__(title="Annonce de mise à jour")
        self.cog = cog
        self.titre = discord.ui.TextInput(label="Titre", max_length=100)
        self.categorie = discord.ui.TextInput(
            label="Catégorie (feature/bugfix/improvement/info)",
            required=False,
            max_length=20,
        )
        self.contenu = discord.ui.TextInput(
            label="Contenu",
            style=discord.TextStyle.paragraph,
            max_length=1900,
        )
        self.ping = discord.ui.TextInput(
            label="Ping (everyone/here/<role_id>)",
            required=False,
            max_length=32,
        )
        self.add_item(self.titre)
        self.add_item(self.categorie)
        self.add_item(self.contenu)
        self.add_item(self.ping)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.cog._handle_submit(
            interaction,
            self.titre.value,
            (self.categorie.value or "").strip().lower() or None,
            self.contenu.value,
            (self.ping.value or "").strip(),
        )


class UpdateAnnounceCog(commands.Cog):
    """Commande slash pour publier des patch-notes premium."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="update", description="Publier une annonce de mise à jour")
    async def update(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(UpdateModal(self))

    async def _handle_submit(
        self,
        interaction: discord.Interaction,
        titre: str,
        categorie: str | None,
        contenu: str,
        ping: str,
    ) -> None:
        if not contenu.strip():
            await safe_respond(
                interaction,
                "❌ Le contenu ne peut pas être vide.",
                ephemeral=True,
            )
            return

        desc, lines = _normalize_lines(contenu)
        if not lines:
            await safe_respond(
                interaction,
                "❌ Aucun point détecté dans le contenu.",
                ephemeral=True,
            )
            return

        version = await _bump_version(categorie)
        now = datetime.now(ZoneInfo("Europe/Paris"))
        date_fr = _format_date_fr(now)

        categories_map: dict[str, list[str]] = {
            "Nouveautés": [],
            "Améliorations": [],
            "Correctifs": [],
            "Notes": [],
        }
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ("ajout", "nouveau", "introduit")):
                categories_map["Nouveautés"].append(line)
            elif any(k in lower for k in ("amélior", "optimis")):
                categories_map["Améliorations"].append(line)
            elif any(k in lower for k in ("bug", "corrig")):
                categories_map["Correctifs"].append(line)
            else:
                categories_map["Notes"].append(line)

        fields: list[tuple[str, str]] = []
        for name in ("Nouveautés", "Améliorations", "Correctifs"):
            if categories_map[name]:
                fields.append((name, "\n".join(categories_map[name])))
        if not fields:
            fields.append(("Notes", "\n".join(categories_map["Notes"])) )
        elif categories_map["Notes"]:
            fields.append(("Notes", "\n".join(categories_map["Notes"])) )

        cat = categorie or "info"
        color = {
            "feature": discord.Color.green(),
            "bugfix": discord.Color.orange(),
            "improvement": discord.Color.blue(),
            "info": discord.Color.dark_gray(),
        }.get(cat, discord.Color.dark_gray())
        emoji = {
            "feature": "🆕",
            "bugfix": "🛠️",
            "improvement": "⚡",
            "info": "ℹ️",
        }.get(cat, "ℹ️")

        embed = discord.Embed(title=f"{emoji} {titre}", color=color)
        if desc:
            embed.description = f"> {desc}"
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text=f"Refuge • v{version} • {date_fr}")
        embed.timestamp = now.astimezone(timezone.utc)

        guild = interaction.guild
        if guild is None:
            await safe_respond(
                interaction,
                "❌ Commande utilisable uniquement sur un serveur.",
                ephemeral=True,
            )
            return

        channel = guild.get_channel(UPDATE_CHANNEL_ID) or interaction.channel
        fallback = False
        if channel is None:
            fallback = True
            channel = interaction.channel

        assert channel is not None
        content_ping = ""
        if ping == "everyone":
            content_ping = "@everyone"
        elif ping == "here":
            content_ping = "@here"
        elif ping.isdigit():
            content_ping = f"<@&{ping}>"

        try:
            msg = await channel.send(content_ping or None, embed=embed)
        except discord.Forbidden:
            logger.warning("Permissions insuffisantes pour envoyer dans %s", channel)
            fallback = True
            channel = interaction.channel
            if channel is None:
                await safe_respond(
                    interaction,
                    "❌ Impossible de publier l'annonce.",
                    ephemeral=True,
                )
                return
            msg = await channel.send(content_ping or None, embed=embed)
        except discord.HTTPException as e:
            await safe_respond(
                interaction,
                f"❌ Échec de l'envoi: {e}",
                ephemeral=True,
            )
            return

        link = msg.jump_url
        if fallback:
            await safe_respond(
                interaction,
                f"Annonce publiée dans {channel.mention} (fallback)\n{link}",
                ephemeral=True,
            )
        else:
            await safe_respond(
                interaction,
                f"Annonce publiée ✔️\n{link}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - load via extension
    await bot.add_cog(UpdateAnnounceCog(bot))

