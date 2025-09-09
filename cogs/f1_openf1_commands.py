from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import commands

from config import DATA_DIR
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir

OPENF1_API = "https://api.openf1.org/v1"
F1_CHANNEL_ID: int = 1413708410330939485
F1_DATA_DIR = os.path.join(DATA_DIR, "f1")
F1_STATE_FILE = os.path.join(F1_DATA_DIR, "openf1_auto.json")
F1_STANDINGS_FILE = os.path.join(F1_DATA_DIR, "f1_standings.json")
ensure_dir(F1_DATA_DIR)

# Approximate color/flag emojis for a few constructors
TEAM_EMOJIS: Dict[str, str] = {
    "Red Bull Racing": "🔵",
    "Ferrari": "🔴",
    "Mercedes": "⚪",
    "McLaren": "🟠",
    "Aston Martin": "🟢",
    "Alpine": "🔷",
    "Williams": "🔵",
    "RB": "🔵",
    "Haas F1 Team": "⚪",
    "Stake F1 Team": "🟢",
}


class F1OpenF1Auto(commands.Cog):
    """Publication automatique d'infos F1 depuis OpenF1."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._task = asyncio.create_task(self._scheduler())
        self._state_lock = asyncio.Lock()

    def cog_unload(self) -> None:
        # Stoppe le scheduler et ferme proprement la session HTTP
        self._task.cancel()
        if self.session and not self.session.closed:
            try:
                asyncio.create_task(self.session.close())
            except RuntimeError:
                # Event loop fermé : on ignore
                pass

    # ── Persistence helpers ────────────────────────────────
    def _read_state(self) -> Dict[str, int]:
        data = read_json_safe(F1_STATE_FILE)
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: Dict[str, int]) -> None:
        atomic_write_json(F1_STATE_FILE, data)

    # ── HTTP helper ────────────────────────────────────────
    async def _fetch_url(self, url: str) -> List[Dict]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "RefugeBot/1.0 (+discord)"},
            )
        async with self.session.get(url) as resp:
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
                # On ignore pour que la boucle continue même en cas d'erreur ponctuelle
                pass
            await asyncio.sleep(3600)  # mise à jour toutes les heures

    async def _post_or_edit(self, key: str, embed: discord.Embed) -> None:
        channel = self.bot.get_channel(F1_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(F1_CHANNEL_ID)
            except Exception:
                return

        async with self._state_lock:
            state = self._read_state()
            msg_id = state.get(key)

        if msg_id:
            try:
                message = await channel.fetch_message(msg_id)  # type: ignore[attr-defined]
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                pass  # Le message a été supprimé : on renverra un nouveau message plus bas
            except Exception:
                pass  # Si autre erreur (permissions, etc.), on tentera d'envoyer un nouveau message

        try:
            message = await channel.send(embed=embed)  # type: ignore[attr-defined]
        except Exception:
            return

        async with self._state_lock:
            state = self._read_state()
            state[key] = message.id
            self._write_state(state)

    # ── Data gathering & embeds ────────────────────────────
    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)  # discord.Embed attend naïf UTC

    async def _update_next(self) -> None:
        year = datetime.utcnow().year
        now = datetime.utcnow()
        url = f"{OPENF1_API}/sessions?year={year}&session_name=Race"
        try:
            sessions = await self._fetch_url(url)
        except aiohttp.ClientError:
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
            embed = discord.Embed(
                title="🏎️ Prochain Grand Prix",
                description="Aucune course à venir",
                color=0xFF1801,
                timestamp=self._utcnow(),
            )
        else:
            start, sess = min(upcoming, key=lambda x: x[0])
            ts = int(start.timestamp())
            circuit = sess.get("circuit_short_name") or "Inconnu"
            meeting = sess.get("meeting_name") or ""
            embed = discord.Embed(
                title=f"🏎️ Prochain Grand Prix — {circuit}",
                description=(
                    f"🗓️ Départ : <t:{ts}:F>\n"
                    f"🌍 {meeting}\n\n"
                    "Préparez vos casques, la tension monte dans les stands !"
                ),
                color=0xFF1801,
                timestamp=self._utcnow(),
            )
        embed.set_footer(text="Données OpenF1")
        await self._post_or_edit("next", embed)

    async def _update_last(self) -> None:
        """Dernière course terminée (Top 10) avec écarts/temps si dispo."""
        year = datetime.utcnow().year
        now = datetime.utcnow()
        sessions_url = f"{OPENF1_API}/sessions?year={year}&session_name=Race"

        try:
            sessions = await self._fetch_url(sessions_url)
        except aiohttp.ClientError:
            return

        past: List[Tuple[datetime, Dict]] = []
        for sess in sessions:
            date_str = sess.get("date_start")
            if not date_str:
                continue
            try:
                start = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start <= now:
                past.append((start, sess))

        if not past:
            embed = discord.Embed(
                title="🏁 Résultats — Dernier Grand Prix",
                description="Course en cours ou pas encore publiée.",
                color=0xFF1801,
                timestamp=self._utcnow(),
            )
            embed.set_footer(text="Dernière mise à jour • Données OpenF1")
            await self._post_or_edit("last", embed)
            return

        last_session = max(past, key=lambda x: x[0])[1]
        skey = last_session.get("session_key")
        if not skey:
            return

        results_url = f"{OPENF1_API}/results?session_key={skey}&position<=10"
        drivers_url = f"{OPENF1_API}/drivers?session_key={skey}"

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
                timestamp=self._utcnow(),
            )
            embed.set_footer(text="Dernière mise à jour • Données OpenF1")
            await self._post_or_edit("last", embed)
            return

        driver_map: Dict[int, Dict[str, str]] = {
            int(d.get("driver_number", 0)): {
                "name": d.get("full_name")
                or d.get("broadcast_name")
                or f"#{d.get('driver_number')}",
                "team": d.get("team_name", "") or "—",
            }
            for d in drivers
        }

        def _safe_int(x, default=0) -> int:
            try:
                return int(x)
            except (TypeError, ValueError):
                return default

        lines: List[str] = []
        for res in sorted(results, key=lambda r: _safe_int(r.get("position"), 99)):
            pos = _safe_int(res.get("position"), 0)
            num = _safe_int(res.get("driver_number"), 0)
            info = driver_map.get(num, {"name": f"#{num}", "team": "—"})
            name = info.get("name", f"#{num}")
            team = info.get("team", "—")
            team_emoji = TEAM_EMOJIS.get(team, "")
            status = str(res.get("status", "")).strip()
            emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(pos, f"{pos}.")
            time_info = res.get("time") or "—"
            if status and status.lower() not in {"finished", "classified"}:
                emoji = "❌"
                time_info = f"Abandon ({status})"
            lines.append(f"{emoji} {team_emoji} {name} ({team}) – {time_info}")

        embed = discord.Embed(
            title="🏁 Résultats — Dernier Grand Prix",
            color=0xFF1801,
            timestamp=self._utcnow(),
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
        sessions_url = f"{OPENF1_API}/sessions?year={year}&session_name=Race"
        try:
            sessions = await self._fetch_url(sessions_url)
        except aiohttp.ClientError:
            return

        standings = read_json_safe(F1_STANDINGS_FILE)
        if not isinstance(standings, dict) or standings.get("year") != year:
            standings = {
                "year": year,
                "drivers": {},
                "constructors": {},
                "processed_sessions": [],
            }
        drivers = standings.get("drivers", {})
        constructors = standings.get("constructors", {})
        processed = set(standings.get("processed_sessions", []))

        def _date_key(sess: Dict) -> datetime:
            ds = sess.get("date_start")
            if not ds:
                return datetime.min
            try:
                return datetime.fromisoformat(ds.replace("Z", "+00:00"))
            except ValueError:
                return datetime.min

        for sess in sorted(sessions, key=_date_key):
            skey = sess.get("session_key")
            if not skey or skey in processed:
                continue
            results_url = f"{OPENF1_API}/results?session_key={skey}"
            drivers_url = f"{OPENF1_API}/drivers?session_key={skey}"
            try:
                results, drivers_info = await asyncio.gather(
                    self._fetch_url(results_url),
                    self._fetch_url(drivers_url),
                )
            except aiohttp.ClientError:
                continue

            driver_map: Dict[int, Dict[str, str]] = {
                int(d.get("driver_number", 0)): {
                    "name": d.get("full_name")
                    or d.get("broadcast_name")
                    or f"#{d.get('driver_number')}",
                    "team": d.get("team_name", "") or "—",
                }
                for d in drivers_info
            }

            for r in results:
                num = r.get("driver_number")
                try:
                    num = int(num)
                except (TypeError, ValueError):
                    continue
                info = driver_map.get(num, {"name": f"#{num}", "team": "—"})
                name = info.get("name", f"#{num}")
                team = info.get("team", "—")
                try:
                    pts = float(r.get("points", 0))
                except (TypeError, ValueError):
                    pts = 0.0

                dkey = f"{year}:{num}"
                d = drivers.setdefault(dkey, {"name": name, "team": team, "points": 0.0})
                d["name"] = name
                d["team"] = team
                d["points"] = float(d.get("points", 0.0)) + pts
                ckey = f"{year}:{team}"
                constructors[ckey] = float(constructors.get(ckey, 0.0)) + pts

            processed.add(skey)

        standings["drivers"] = drivers
        standings["constructors"] = constructors
        standings["processed_sessions"] = list(processed)
        standings["year"] = year
        atomic_write_json(F1_STANDINGS_FILE, standings)

        driver_lines: List[str] = []
        top_drivers = [
            d for k, d in drivers.items() if k.startswith(f"{year}:")
        ]
        top_drivers.sort(key=lambda x: float(x["points"]), reverse=True)
        top_drivers = top_drivers[:10]
        for i, d in enumerate(top_drivers, start=1):
            pts_val = float(d["points"])
            pts = int(pts_val) if pts_val.is_integer() else round(pts_val, 1)
            team_emoji = TEAM_EMOJIS.get(d["team"], "")
            driver_lines.append(
                f"{i}. {team_emoji} **{d['name']}** ({d['team']}) — **{pts}**"
            )

        constructor_lines: List[str] = []
        top_teams = [
            (team_key.split(":", 1)[1], pts)
            for team_key, pts in constructors.items()
            if team_key.startswith(f"{year}:")
        ]
        top_teams.sort(key=lambda x: float(x[1]), reverse=True)
        top_teams = top_teams[:10]
        for i, (team, pts) in enumerate(top_teams, start=1):
            pts_fmt = int(pts) if float(pts).is_integer() else round(float(pts), 1)
            team_emoji = TEAM_EMOJIS.get(team, "")
            constructor_lines.append(
                f"{i}. {team_emoji} **{team}** — **{pts_fmt}**"
            )

        embed = discord.Embed(
            color=0xFF1801,
            timestamp=self._utcnow(),
        )
        embed.add_field(
            name=f"🏆 Championnat Pilotes {year}",
            value="\n".join(driver_lines) or "—",
            inline=False,
        )
        embed.add_field(
            name=f"🏭 Constructeurs {year}",
            value="\n".join(constructor_lines) or "—",
            inline=False,
        )
        embed.set_footer(text="Classement calculé d’après les résultats OpenF1")
        await self._post_or_edit("standings", embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(F1OpenF1Auto(bot))