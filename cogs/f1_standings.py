"""Surveillance automatique des sessions F1.

Cette cog interroge l'API Ergast afin de détecter les résultats des
sessions de Formule 1 et met à jour un message unique par type de
session dans un salon Discord dédié.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord.ext import commands

from config import DATA_DIR
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir

logger = logging.getLogger(__name__)

# Constants
F1_CHANNEL_ID: int = 1413708410330939485
ERGAST_API = "https://ergast.com/api/f1"
F1_DATA_DIR = os.path.join(DATA_DIR, "f1")
F1_STATE_FILE = os.path.join(F1_DATA_DIR, "f1_state.json")
ensure_dir(F1_DATA_DIR)

# Session types à surveiller
SESSION_TYPES = {
    "fp1": {"name": "🏁 Essais Libres 1", "emoji": "🔵"},
    "fp2": {"name": "🏁 Essais Libres 2", "emoji": "🔵"},
    "fp3": {"name": "🏁 Essais Libres 3", "emoji": "🔵"},
    "qualifying": {"name": "⚡ Qualifications", "emoji": "🟡"},
    "sprint": {"name": "🏃 Course Sprint", "emoji": "🟠"},
    "race": {"name": "🏆 Course Principale", "emoji": "🔴"},
}


class F1Standings(commands.Cog):
    """Cog de surveillance des sessions F1."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._current_year = datetime.now().year
        # Démarrer la surveillance
        self._task = asyncio.create_task(self._f1_monitor())

    def cog_unload(self) -> None:
        # Annuler la tâche et fermer la session HTTP
        if self._task:
            self._task.cancel()
        if self.session and not self.session.closed:
            try:
                asyncio.create_task(self.session.close())
            except RuntimeError:
                # Pas de boucle d'événements active
                pass

    # ── Méthodes de persistance ──────────────────────────────
    def _read_state(self) -> Dict[str, Any]:
        return read_json_safe(F1_STATE_FILE)

    def _write_state(self, data: Dict[str, Any]) -> None:
        atomic_write_json(F1_STATE_FILE, data)

    # ── Boucle principale ────────────────────────────────────
    async def _f1_monitor(self) -> None:
        """Surveillance principale F1 - boucle infinie."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                if await self._is_f1_weekend():
                    await self._check_all_sessions()
            except Exception as e:  # pragma: no cover - best effort logging
                logger.exception("Erreur monitoring F1: %s", e)
            await asyncio.sleep(30)

    async def _check_all_sessions(self) -> None:
        """Vérifie toutes les sessions définies."""
        for stype in SESSION_TYPES.keys():
            results = await self._get_session_results(stype)
            if results:
                await self._update_session_message(stype, results)

    # ── Gestion des messages ─────────────────────────────────
    async def _update_session_message(self, session_type: str, results: List[Dict]) -> None:
        """Met à jour ou crée le message pour une session."""
        channel = self.bot.get_channel(F1_CHANNEL_ID)
        if not channel:
            return

        state = self._read_state()
        messages = state.get("messages", {})

        # Créer l'embed
        embed = await self._create_session_embed(session_type, results)

        # Si message existe, le modifier
        if session_type in messages and messages[session_type].get("message_id"):
            try:
                message = await channel.fetch_message(messages[session_type]["message_id"])
                await message.edit(embed=embed)
                logger.info("Message %s modifié", session_type)
            except discord.NotFound:
                # Message supprimé, en créer un nouveau
                message = await channel.send(embed=embed)
                messages[session_type] = {"message_id": message.id}
        else:
            # Créer nouveau message
            message = await channel.send(embed=embed)
            messages.setdefault(session_type, {})["message_id"] = message.id

        # Sauvegarder l'état
        messages[session_type]["last_update"] = datetime.now().isoformat()
        state["messages"] = messages
        self._write_state(state)

    async def _create_session_embed(self, session_type: str, results: List[Dict]) -> discord.Embed:
        """Crée l'embed pour une session."""
        session_info = SESSION_TYPES[session_type]

        # Déterminer le GP actuel
        gp_name = await self._get_current_gp_name()

        embed = discord.Embed(
            title=f"{session_info['emoji']} {session_info['name']} - GP {gp_name} {self._current_year}",
            color=0xFF1801,  # Rouge F1
            timestamp=datetime.now(),
        )

        # Ajouter les résultats
        if results:
            value_lines = []
            for i, result in enumerate(results[:20]):  # Top 20
                pos = i + 1
                driver = result.get("driver", "Unknown")
                team = result.get("constructor", "Unknown")
                time_str = result.get("time", "No Time")

                # Émojis pour le podium
                if pos == 1:
                    emoji = "🥇"
                elif pos == 2:
                    emoji = "🥈"
                elif pos == 3:
                    emoji = "🥉"
                else:
                    emoji = f"{pos}."

                value_lines.append(f"{emoji} **{driver}** ({team}) - `{time_str}`")

            embed.add_field(
                name="🏁 Classement",
                value="\n".join(value_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="ℹ️ Statut",
                value="Session en cours ou pas encore démarrée",
                inline=False,
            )

        embed.set_footer(text="Dernière mise à jour")
        return embed

    # ── API Ergast ───────────────────────────────────────────
    async def _get_session_results(self, session_type: str) -> Optional[List[Dict]]:
        """Récupère les résultats d'une session via l'API Ergast."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        if session_type == "race":
            url = f"{ERGAST_API}/{self._current_year}/last/results.json"
        elif session_type == "qualifying":
            url = f"{ERGAST_API}/{self._current_year}/last/qualifying.json"
        elif session_type == "sprint":
            url = f"{ERGAST_API}/{self._current_year}/last/sprint.json"
        elif session_type in ["fp1", "fp2", "fp3"]:
            session_num = session_type[-1]
            url = f"{ERGAST_API}/{self._current_year}/last/practice/{session_num}.json"
        else:
            return None

        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_ergast_results(data, session_type)
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API Ergast %s: %s", session_type, e)
        return None

    def _parse_ergast_results(self, data: Dict, session_type: str) -> List[Dict]:
        """Parse les données Ergast en format standard."""
        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not races:
            return []
        race = races[0]
        parsed: List[Dict[str, str]] = []

        if session_type == "qualifying":
            results = race.get("QualifyingResults", [])
            for r in results:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                time = r.get("Q3") or r.get("Q2") or r.get("Q1") or "No Time"
                parsed.append(
                    {
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "constructor": constructor.get("name", ""),
                        "time": time,
                    }
                )
        elif session_type == "race":
            results = race.get("Results", [])
            for r in results:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                time = r.get("Time", {}).get("time") or r.get("status", "")
                parsed.append(
                    {
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "constructor": constructor.get("name", ""),
                        "time": time,
                    }
                )
        elif session_type == "sprint":
            results = race.get("SprintResults", [])
            for r in results:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                time = r.get("Time", {}).get("time") or r.get("status", "")
                parsed.append(
                    {
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "constructor": constructor.get("name", ""),
                        "time": time,
                    }
                )
        else:  # FP1/FP2/FP3
            results = race.get("Results", [])
            for r in results:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                time = r.get("Time", {}).get("time") or "No Time"
                parsed.append(
                    {
                        "driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                        "constructor": constructor.get("name", ""),
                        "time": time,
                    }
                )

        return parsed

    # ── Détection week-end F1 ────────────────────────────────
    async def _is_f1_weekend(self) -> bool:
        """Vérifie si nous sommes dans un week-end F1."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{ERGAST_API}/{self._current_year}.json"
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API calendrier F1: %s", e)
            return False

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        now = datetime.now(timezone.utc)
        for race in races:
            date_str = race.get("date")
            time_str = race.get("time", "00:00:00Z")
            if not date_str:
                continue
            try:
                race_dt = datetime.fromisoformat(f"{date_str}T{time_str.replace('Z', '+00:00')}")
            except Exception:
                try:
                    race_dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                except Exception:
                    continue
            if abs((race_dt - now).days) <= 3:
                gp_name = race.get("raceName", "")
                state = self._read_state()
                state["current_gp"] = gp_name
                self._write_state(state)
                return True
        return False

    async def _get_current_gp_name(self) -> str:
        """Retourne le nom du GP actuel."""
        state = self._read_state()
        if state.get("current_gp"):
            return state["current_gp"]
        # Essayer de déterminer à partir du calendrier
        try:
            await self._is_f1_weekend()
            state = self._read_state()
            return state.get("current_gp", "Inconnu")
        except Exception:
            return "Inconnu"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(F1Standings(bot))
