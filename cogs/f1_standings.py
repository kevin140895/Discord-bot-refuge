"""Surveillance automatique des sessions F1.

Cette cog interroge l'API `openf1` afin de détecter les résultats des
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
OPENF1_API = "https://api.openf1.org/v1"
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
        self._driver_cache: Dict[int, Dict[str, str]] = {}
        self._state_lock = asyncio.Lock()
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

        async with self._state_lock:
            state = self._read_state()
            messages = state.get("messages", {})
            msg_id = messages.get(session_type, {}).get("message_id")

        # Créer l'embed
        embed = await self._create_session_embed(session_type, results)

        if msg_id:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(embed=embed)
                logger.info("Message %s modifié", session_type)
                new_id = msg_id
            except discord.NotFound:
                message = await channel.send(embed=embed)
                new_id = message.id
        else:
            message = await channel.send(embed=embed)
            new_id = message.id

        async with self._state_lock:
            state = self._read_state()
            messages = state.get("messages", {})
            entry = messages.setdefault(session_type, {})
            entry["message_id"] = new_id
            entry["last_update"] = datetime.now().isoformat()
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

    # ── API OpenF1 ───────────────────────────────────────────
    async def _get_session_results(self, session_type: str) -> Optional[List[Dict]]:
        """Récupère les résultats d'une session via l'API OpenF1."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        params = {"year": str(self._current_year)}

        if session_type == "race":
            params["session_type"] = "Race"
        elif session_type == "qualifying":
            params["session_type"] = "Qualifying"
        elif session_type == "sprint":
            params["session_type"] = "Sprint"
        elif session_type in ("fp1", "fp2", "fp3"):
            params["session_type"] = "Practice"
            params["session_name"] = f"Practice {session_type[-1]}"
        else:
            return None

        # Construire l'URL de sessions
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{OPENF1_API}/sessions?{query}"

        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                sessions = await response.json()
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API OpenF1 sessions %s: %s", session_type, e)
            return None

        if not sessions:
            return None

        # Prendre la session la plus récente
        latest = sorted(sessions, key=lambda s: s.get("session_key", 0))[-1]
        session_key = latest.get("session_key")
        if not session_key:
            return None

        try:
            pos_url = f"{OPENF1_API}/position?session_key={session_key}"
            async with self.session.get(pos_url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                positions = await resp.json()
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API OpenF1 positions %s: %s", session_type, e)
            return None

        return await self._parse_openf1_positions(positions)

    async def _parse_openf1_positions(self, positions: List[Dict]) -> List[Dict]:
        """Convertit les données de position OpenF1 dans un format commun."""
        # L'API renvoie plusieurs lignes par pilote au fil de la session.
        # On ne conserve que la dernière mise à jour pour chaque numéro.
        latest: Dict[int, Dict] = {}

        def _time_key(entry: Dict) -> float:
            """Retourne un ordre approximatif pour déterminer la plus récente."""
            date = entry.get("date") or entry.get("session_time")
            if isinstance(date, str):
                try:
                    return datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
                except Exception:  # pragma: no cover - format inattendu
                    return 0.0
            try:
                return float(date)
            except (TypeError, ValueError):
                return 0.0

        for p in sorted(positions, key=_time_key):
            try:
                num = int(p.get("driver_number"))
            except (TypeError, ValueError):
                continue
            latest[num] = p  # la dernière entrée (triée) reste

        parsed: List[Dict[str, str]] = []
        for p in sorted(latest.values(), key=lambda r: r.get("position", 0))[:20]:
            driver_num = p.get("driver_number")
            info = await self._get_driver_info(driver_num)
            time_str = "No Time"
            for key in ("best_lap_time", "time", "interval", "gap_to_leader"):
                val = p.get(key)
                if val is not None and val != "":
                    time_str = str(val)
                    break
            parsed.append(
                {
                    "driver": info.get("name", f"#{driver_num}"),
                    "constructor": info.get("team", "Unknown"),
                    "time": time_str,
                }
            )
        return parsed

    async def _get_driver_info(self, driver_number: int) -> Dict[str, str]:
        """Récupère les informations d'un pilote via OpenF1."""
        if driver_number in self._driver_cache:
            return self._driver_cache[driver_number]

        url = f"{OPENF1_API}/drivers?driver_number={driver_number}"
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API OpenF1 driver %s: %s", driver_number, e)
            return {}

        if not data:
            return {}

        driver = data[0]
        name = (
            driver.get("full_name")
            or driver.get("name")
            or f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip()
        )
        team = driver.get("team_name") or driver.get("team") or driver.get("constructor")
        info = {"name": name, "team": team}
        self._driver_cache[driver_number] = info
        return info

    # ── Détection week-end F1 ────────────────────────────────
    async def _is_f1_weekend(self) -> bool:
        """Vérifie si nous sommes dans un week-end F1."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{OPENF1_API}/sessions?session_type=Race&year={self._current_year}"
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return False
                races = await resp.json()
        except Exception as e:  # pragma: no cover - network dependent
            logger.error("Erreur API calendrier F1: %s", e)
            return False

        now = datetime.now(timezone.utc)
        for race in races:
            start = (
                race.get("date_start")
                or race.get("session_start")
                or race.get("start_time")
                or race.get("date")
            )
            if not start:
                continue
            try:
                race_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            except Exception:
                continue
            if abs((race_dt - now).days) <= 3:
                gp_name = race.get("meeting_name") or race.get("country_name") or ""
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
