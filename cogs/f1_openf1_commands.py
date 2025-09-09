from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
import discord
from discord.ext import commands

from config import DATA_DIR
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir

OPENF1_API = "https://api.openf1.org/v1"
F1_CHANNEL_ID: int = 1413708410330939485
F1_DATA_DIR = os.path.join(DATA_DIR, "f1")
F1_STATE_FILE = os.path.join(F1_DATA_DIR, "openf1_auto.json")
ensure_dir(F1_DATA_DIR)


class F1OpenF1Auto(commands.Cog):
    """Publication automatique d'infos F1 depuis OpenF1."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._task = asyncio.create_task(self._scheduler())

    def cog_unload(self) -> None:
        self._task.cancel()
        if self.session and not self.session.closed:
            try:
                asyncio.create_task(self.session.close())
            except RuntimeError:
                pass

    # ── Persistence helpers ────────────────────────────────
    def _read_state(self) -> Dict[str, int]:
        return read_json_safe(F1_STATE_FILE)

    def _write_state(self, data: Dict[str, int]) -> None:
        atomic_write_json(F1_STATE_FILE, data)

    # ── HTTP helper ────────────────────────────────────────
    async def _fetch_url(self, url: str) -> List[Dict]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.get(url, timeout=10) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── Scheduler ──────────────────────────────────────────
    async def _scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await asyncio.gather(
                    self._update_next(),
                    self._update_last(),
                    self._update_standings(),
                )
            except Exception:
                pass
            await asyncio.sleep(6 * 3600)  # mise à jour toutes les 6h

    async def _post_or_edit(self, key: str, embed: discord.Embed) -> None:
        channel = self.bot.get_channel(F1_CHANNEL_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(F1_CHANNEL_ID)
            except Exception:
                return
        state = self._read_state()
        msg_id = state.get(key)
        if msg_id:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                pass
        message = await channel.send(embed=embed)
        state[key] = message.id
        self._write_state(state)

    # ── Data gathering & embeds ────────────────────────────
    async def _update_next(self) -> None:
        year = datetime.utcnow().year
        now = datetime.utcnow()
        url = f"{OPENF1_API}/sessions?year={year}&session_name=Race"
        try:
            sessions = await self._fetch_url(url)
        except aiohttp.ClientError:
            return
        upcoming = []
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
            embed = discord.Embed(
                title="🏎️ Prochain Grand Prix",
                description="Aucune course à venir",
                color=0xFF1801,
                timestamp=datetime.utcnow(),
            )
        else:
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
        await self._post_or_edit("next", embed)

    async def _update_last(self) -> None:
        results_url = f"{OPENF1_API}/session_result?session_key=LATEST&position<=10"
        drivers_url = f"{OPENF1_API}/drivers?session_key=LATEST"
        try:
            results, drivers = await asyncio.gather(
                self._fetch_url(results_url),
                self._fetch_url(drivers_url),
            )
        except aiohttp.ClientError:
            return
        if not results:
            embed = discord.Embed(
                title="🏁 Résultats — Dernier Grand Prix",
                description="Course en cours ou pas encore publiée.",
                color=0xFF1801,
                timestamp=datetime.utcnow(),
            )
            embed.set_footer(text="Dernière mise à jour • Données OpenF1")
            await self._post_or_edit("last", embed)
            return
        driver_map = {
            int(d.get("driver_number", 0)): {
                "name": d.get("full_name")
                or d.get("broadcast_name")
                or f"#{d.get('driver_number')}",
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
        await self._post_or_edit("last", embed)

    async def _update_standings(self) -> None:
        year = datetime.utcnow().year
        url = f"{OPENF1_API}/session_result?year={year}&session_name=Race"
        try:
            results = await self._fetch_url(url)
        except aiohttp.ClientError:
            return
        if not results:
            embed = discord.Embed(
                title=f"🏆 Championnat Pilotes {year}",
                description="Aucun résultat disponible pour cette année.",
                color=0xFF1801,
                timestamp=datetime.utcnow(),
            )
            embed.set_footer(text="Classement calculé d’après les résultats OpenF1")
            await self._post_or_edit("standings", embed)
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
            sorted(drivers.values(), key=lambda x: x["points"], reverse=True)[:10],
            start=1,
        ):
            pts = int(d["points"]) if d["points"].is_integer() else round(d["points"], 1)
            driver_lines.append(f"{i}. **{d['name']}** ({d['team']}) — **{pts}**")
        constructor_lines = []
        for i, (team, pts) in enumerate(
            sorted(constructors.items(), key=lambda x: x[1], reverse=True)[:10],
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
        await self._post_or_edit("standings", embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(F1OpenF1Auto(bot))
