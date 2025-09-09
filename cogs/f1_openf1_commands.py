from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

OPENF1_API = "https://api.openf1.org/v1"


class F1OpenF1Commands(commands.Cog):
    """Commandes slash F1 basées sur l'API OpenF1."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def _fetch_url(self, url: str) -> List[Dict]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.get(url, timeout=10) as resp:
            resp.raise_for_status()
            return await resp.json()

    def cog_unload(self) -> None:
        if self.session and not self.session.closed:
            try:
                asyncio.create_task(self.session.close())
            except RuntimeError:
                pass
        self.bot.tree.remove_command(self.group.name)

    group = app_commands.Group(name="f1", description="Infos Formule 1 via OpenF1")

    @group.command(name="next", description="Prochaine course F1")
    @app_commands.describe(year="Année souhaitée (écart par défaut : année courante)")
    async def next(self, interaction: discord.Interaction, year: Optional[int] = None) -> None:
        await interaction.response.defer()
        year = year or datetime.utcnow().year
        now = datetime.utcnow()
        url = f"{OPENF1_API}/sessions?year={year}&session_name=Race"
        try:
            sessions = await self._fetch_url(url)
        except aiohttp.ClientError:
            await interaction.followup.send(
                "⛔ OpenF1 indisponible pour le moment. Réessaie plus tard.",
                ephemeral=True,
            )
            return
        upcoming: List[Tuple[datetime, Dict]] = []
        for sess in sessions:
            date_str = sess.get("date_start")
            if not date_str:
                continue
            try:
                start = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start > now:
                upcoming.append((start, sess))
        if not upcoming:
            await interaction.followup.send("Aucune course à venir.", ephemeral=True)
            return
        start, sess = min(upcoming, key=lambda x: x[0])
        ts = int(start.timestamp())
        circuit = sess.get("circuit_short_name", "Inconnu")
        meeting = sess.get("meeting_name", "")
        embed = discord.Embed(
            title=f"🏎️ Prochain Grand Prix — {circuit}",
            description=(
                f"🗓️ Départ : <t:{ts}:F>\n"
                f"🌍 {meeting}\n\n"
                "Préparez vos casques, la tension monte dans les stands !"
            ),
            color=0xFF1801,
            timestamp=datetime.utcnow(),
        )
        embed.set_footer(text="Données OpenF1")
        await interaction.followup.send(embed=embed)

    @group.command(name="last", description="Résultats du dernier Grand Prix")
    async def last(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        results_url = f"{OPENF1_API}/session_result?session_key=LATEST&position<=10"
        drivers_url = f"{OPENF1_API}/drivers?session_key=LATEST"
        try:
            results = await self._fetch_url(results_url)
            drivers = await self._fetch_url(drivers_url)
        except aiohttp.ClientError:
            await interaction.followup.send(
                "⛔ OpenF1 indisponible pour le moment. Réessaie plus tard.",
                ephemeral=True,
            )
            return
        if not results:
            await interaction.followup.send(
                "Course en cours ou pas encore publiée.",
                ephemeral=True,
            )
            return
        driver_map = {
            int(d.get("driver_number", 0)): {
                "name": d.get("full_name") or d.get("broadcast_name") or f"#{d.get('driver_number')}",
                "team": d.get("team_name", ""),
            }
            for d in drivers
        }
        lines = []
        for res in sorted(results, key=lambda r: int(r.get("position", 0))):
            pos = int(res.get("position", 0))
            num = int(res.get("driver_number", 0))
            points = res.get("points", 0)
            info = driver_map.get(num, {})
            name = info.get("name", f"#{num}")
            team = info.get("team", "—")
            emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"{pos}.")
            lines.append(f"{emoji} **{name}** ({team}) — {points} pts")
        embed = discord.Embed(
            title="🏁 Résultats — Dernier Grand Prix",
            color=0xFF1801,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name="🏆 Podium & Top 10",
            value="\n".join(lines),
            inline=False,
        )
        embed.set_footer(text="Dernière mise à jour • Données OpenF1")
        await interaction.followup.send(embed=embed)

    @group.command(name="standings", description="Classements pilotes et constructeurs")
    @app_commands.describe(
        year="Année du championnat (défaut : année courante)",
        limit="Nombre de lignes à afficher (1-20)",
    )
    async def standings(
        self,
        interaction: discord.Interaction,
        year: Optional[int] = None,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        await interaction.response.defer()
        year = year or datetime.utcnow().year
        url = f"{OPENF1_API}/session_result?year={year}&session_name=Race"
        try:
            results = await self._fetch_url(url)
        except aiohttp.ClientError:
            await interaction.followup.send(
                "⛔ OpenF1 indisponible pour le moment. Réessaie plus tard.",
                ephemeral=True,
            )
            return
        if not results:
            await interaction.followup.send(
                "Aucun résultat disponible pour cette année.",
                ephemeral=True,
            )
            return
        drivers: Dict[int, Dict[str, Optional[str]]] = {}
        constructors: Dict[str, float] = {}
        for r in results:
            num = int(r.get("driver_number", 0))
            name = r.get("full_name") or r.get("broadcast_name") or f"#{num}"
            team = r.get("team_name", "")
            pts = float(r.get("points", 0))
            d = drivers.setdefault(num, {"name": name, "team": team, "points": 0.0})
            d["points"] += pts
            constructors[team] = constructors.get(team, 0.0) + pts
        driver_lines = []
        for i, d in enumerate(
            sorted(drivers.values(), key=lambda x: x["points"], reverse=True)[: limit],
            start=1,
        ):
            pts = int(d["points"]) if d["points"].is_integer() else round(d["points"], 1)
            driver_lines.append(f"{i}. **{d['name']}** ({d['team']}) — **{pts}**")
        constructor_lines = []
        for i, (team, pts) in enumerate(
            sorted(constructors.items(), key=lambda x: x[1], reverse=True)[: limit],
            start=1,
        ):
            pts_fmt = int(pts) if pts.is_integer() else round(pts, 1)
            constructor_lines.append(f"{i}. **{team}** — **{pts_fmt}**")
        embed = discord.Embed(
            color=0xFF1801,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name=f"🏆 Championnat Pilotes {year}",
            value="\n".join(driver_lines),
            inline=False,
        )
        embed.add_field(
            name=f"🏭 Constructeurs {year}",
            value="\n".join(constructor_lines),
            inline=False,
        )
        embed.set_footer(text="Classement calculé d’après les résultats OpenF1")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    cog = F1OpenF1Commands(bot)
    await bot.add_cog(cog)
    bot.tree.remove_command(cog.group.name)
    bot.tree.add_command(cog.group)
