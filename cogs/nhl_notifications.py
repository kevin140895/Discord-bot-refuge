"""Automated NHL live scores, betting previews, and injury alerts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import contextlib
import logging
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import BOT_ALERTS_CHANNEL_ID, DATA_DIR
from utils.rate_limit import limiter

logger = logging.getLogger(__name__)

NHL_LIVE_CHANNEL_ID = int(os.getenv("NHL_LIVE_CHANNEL_ID", "1460631845640208427"))
NHL_KEY_PLAYERS = {
    name.strip().lower()
    for name in os.getenv("NHL_KEY_PLAYERS", "").split(",")
    if name.strip()
}
ODDS_API_URL = os.getenv("NHL_ODDS_API_URL")
ODDS_API_KEY = os.getenv("NHL_ODDS_API_KEY")
INJURIES_API_URL = os.getenv("NHL_INJURIES_API_URL")

SCHEDULE_API = "https://statsapi.web.nhl.com/api/v1/schedule"
TEAM_STATS_API = "https://statsapi.web.nhl.com/api/v1/teams/{team_id}/stats"


@dataclass
class MatchInfo:
    match_id: str
    home_team: str
    away_team: str
    start_time: datetime
    status: str
    home_score: int
    away_score: int
    home_id: int
    away_id: int

    @property
    def score_label(self) -> str:
        return f"{self.away_score}-{self.home_score}"


class NHLDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                start_time TEXT NOT NULL,
                status TEXT NOT NULL,
                scores TEXT NOT NULL,
                notified_preview INTEGER DEFAULT 0,
                notified_2h INTEGER DEFAULT 0,
                notified_final INTEGER DEFAULT 0,
                notified_upcoming INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                total_bets INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                roi REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS bets (
                bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                match_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                odds REAL NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS injuries (
                injury_id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                team TEXT NOT NULL,
                status TEXT NOT NULL,
                impact TEXT NOT NULL,
                notified INTEGER DEFAULT 0
            );
            """
        )
        self._conn.commit()

    async def execute(self, query: str, params: Tuple[Any, ...] = ()) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_sync, query, params)

    def _execute_sync(self, query: str, params: Tuple[Any, ...]) -> None:
        cursor = self._conn.cursor()
        cursor.execute(query, params)
        self._conn.commit()

    async def fetchone(
        self, query: str, params: Tuple[Any, ...] = ()
    ) -> Optional[sqlite3.Row]:
        async with self._lock:
            return await asyncio.to_thread(self._fetchone_sync, query, params)

    def _fetchone_sync(
        self, query: str, params: Tuple[Any, ...]
    ) -> Optional[sqlite3.Row]:
        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)


class MessageQueue:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queue: asyncio.Queue[Tuple[int, str]] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._last_sent: Dict[int, datetime] = {}
        self._min_interval = timedelta(seconds=2)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def enqueue(self, channel_id: int, message: str) -> None:
        await self.queue.put((channel_id, message))

    async def _worker(self) -> None:
        while True:
            channel_id, message = await self.queue.get()
            try:
                await self._send_message(channel_id, message)
            finally:
                self.queue.task_done()

    async def _send_message(self, channel_id: int, message: str) -> None:
        last_sent = self._last_sent.get(channel_id)
        now = datetime.now(timezone.utc)
        if last_sent and now - last_sent < self._min_interval:
            await asyncio.sleep((self._min_interval - (now - last_sent)).total_seconds())

        await limiter.acquire(bucket=f"channel:{channel_id}")
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                logger.warning("Unable to fetch channel %s: %s", channel_id, exc)
                return

        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("Channel %s is not messageable", channel_id)
            return

        try:
            await channel.send(message)
            self._last_sent[channel_id] = datetime.now(timezone.utc)
        except discord.HTTPException as exc:
            logger.warning("Failed to send message to %s: %s", channel_id, exc)


class NHLNotificationsCog(commands.Cog):
    """NHL live scores, betting previews, and injury alerts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        os.makedirs(DATA_DIR, exist_ok=True)
        db_path = os.path.join(DATA_DIR, "nhl_notifications.sqlite")
        self.db = NHLDatabase(db_path)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.queue = MessageQueue(bot)
        self.queue.start()
        self._xg_cache: Dict[int, Tuple[float, datetime]] = {}

        self.live_score_task.start()
        self.betting_alerts_task.start()
        self.injury_task.start()

    async def cog_unload(self) -> None:
        self.live_score_task.cancel()
        self.betting_alerts_task.cancel()
        self.injury_task.cancel()
        await self.session.close()
        await self.db.close()
        await self.queue.stop()

    async def _fetch_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        backoff = 1.5
        for attempt in range(1, 4):
            try:
                await limiter.acquire(bucket="global")
                async with self.session.get(url, params=params) as response:
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt >= 3:
                    logger.error("API request failed after retries: %s", exc)
                    raise
                sleep_for = backoff ** attempt
                logger.warning("API request failed (attempt %s): %s", attempt, exc)
                await asyncio.sleep(sleep_for)
        raise RuntimeError("Unreachable retry loop")

    async def _send_admin_alert(self, message: str) -> None:
        if BOT_ALERTS_CHANNEL_ID:
            await self.queue.enqueue(BOT_ALERTS_CHANNEL_ID, f"⚠️ {message}")

    def _parse_games(self, payload: Dict[str, Any]) -> List[MatchInfo]:
        games: List[MatchInfo] = []
        for date_block in payload.get("dates", []):
            for game in date_block.get("games", []):
                teams = game.get("teams", {})
                home = teams.get("home", {})
                away = teams.get("away", {})
                home_team = home.get("team", {})
                away_team = away.get("team", {})
                status = game.get("status", {}).get("detailedState", "Unknown")
                start_time = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
                games.append(
                    MatchInfo(
                        match_id=str(game.get("gamePk")),
                        home_team=home_team.get("abbreviation", home_team.get("name", "HOME")),
                        away_team=away_team.get("abbreviation", away_team.get("name", "AWAY")),
                        start_time=start_time,
                        status=status,
                        home_score=int(home.get("score", 0)),
                        away_score=int(away.get("score", 0)),
                        home_id=int(home_team.get("id", 0)),
                        away_id=int(away_team.get("id", 0)),
                    )
                )
        return games

    async def _get_match_row(self, match_id: str) -> Optional[sqlite3.Row]:
        return await self.db.fetchone("SELECT * FROM matches WHERE match_id = ?", (match_id,))

    async def _upsert_match(self, match: MatchInfo) -> None:
        await self.db.execute(
            """
            INSERT INTO matches (match_id, home_team, away_team, start_time, status, scores)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                home_team=excluded.home_team,
                away_team=excluded.away_team,
                start_time=excluded.start_time,
                status=excluded.status,
                scores=excluded.scores
            """,
            (
                match.match_id,
                match.home_team,
                match.away_team,
                match.start_time.isoformat(),
                match.status,
                match.score_label,
            ),
        )

    async def _fetch_schedule(self) -> List[MatchInfo]:
        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        payload = await self._fetch_json(
            SCHEDULE_API,
            params={
                "startDate": today.isoformat(),
                "endDate": tomorrow.isoformat(),
                "expand": "schedule.linescore",
            },
        )
        return self._parse_games(payload)

    async def _fetch_team_xg(self, team_id: int) -> Optional[float]:
        if not team_id:
            return None
        cached = self._xg_cache.get(team_id)
        if cached and datetime.now(timezone.utc) - cached[1] < timedelta(hours=12):
            return cached[0]

        payload = await self._fetch_json(TEAM_STATS_API.format(team_id=team_id))
        splits = payload.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        stats = splits[0].get("stat", {})
        goals_per_game = stats.get("goalsPerGame")
        if goals_per_game is None:
            return None
        xg_value = float(goals_per_game)
        self._xg_cache[team_id] = (xg_value, datetime.now(timezone.utc))
        return xg_value

    async def _fetch_odds(self, match: MatchInfo) -> str:
        if not ODDS_API_URL or not ODDS_API_KEY:
            return "Cotes indisponibles"
        try:
            payload = await self._fetch_json(
                ODDS_API_URL,
                params={
                    "home": match.home_team,
                    "away": match.away_team,
                    "api_key": ODDS_API_KEY,
                },
            )
        except Exception as exc:
            logger.warning("Odds API error: %s", exc)
            return "Cotes indisponibles"

        if isinstance(payload, dict) and payload.get("odds"):
            odds = payload["odds"]
            home = odds.get("home")
            away = odds.get("away")
            if home and away:
                return f"{match.away_team} {away} | {match.home_team} {home}"
        return "Cotes indisponibles"

    async def _fetch_injuries(self) -> Iterable[Dict[str, str]]:
        if not INJURIES_API_URL:
            return []
        try:
            payload = await self._fetch_json(INJURIES_API_URL)
        except Exception as exc:
            logger.warning("Injuries API error: %s", exc)
            return []

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("injuries", [])
        return []

    async def _announce_live_game(
        self, match: MatchInfo, row: Optional[sqlite3.Row]
    ) -> None:
        current_score = match.score_label
        stored_score = row["scores"] if row else ""
        if current_score == stored_score and match.status != "Final":
            return
        if match.status == "Final" and row and row["notified_final"]:
            return

        emoji = "🔴 LIVE" if match.status != "Final" else "✅ FINAL"
        message = f"{emoji} | {match.away_team} @ {match.home_team} | {current_score}"
        await self.queue.enqueue(NHL_LIVE_CHANNEL_ID, message)

        if match.status == "Final":
            await self.db.execute(
                "UPDATE matches SET notified_final = 1 WHERE match_id = ?",
                (match.match_id,),
            )

    async def _maybe_send_preview(self, match: MatchInfo, row: Optional[sqlite3.Row]) -> None:
        now = datetime.now(timezone.utc)
        delta = match.start_time - now
        if delta <= timedelta(hours=8, minutes=30) and delta >= timedelta(hours=7, minutes=30):
            if row and row["notified_preview"]:
                return
            home_xg = await self._fetch_team_xg(match.home_id)
            away_xg = await self._fetch_team_xg(match.away_id)
            odds_line = await self._fetch_odds(match)
            xg_line = "xG indisponible"
            if home_xg is not None and away_xg is not None:
                xg_line = f"{match.away_team} {away_xg:.2f} | {match.home_team} {home_xg:.2f}"
            message = (
                f"💰 PARIS | {match.away_team} @ {match.home_team}\n"
                f"📊 xG | {xg_line}\n"
                f"✅ +EV PICKS | {odds_line}"
            )
            await self.queue.enqueue(NHL_LIVE_CHANNEL_ID, message)
            await self.db.execute(
                "UPDATE matches SET notified_preview = 1 WHERE match_id = ?",
                (match.match_id,),
            )

    async def _maybe_send_last_call(self, match: MatchInfo, row: Optional[sqlite3.Row]) -> None:
        now = datetime.now(timezone.utc)
        delta = match.start_time - now
        if delta <= timedelta(hours=2, minutes=30) and delta >= timedelta(hours=1, minutes=30):
            if row and row["notified_2h"]:
                return
            odds_line = await self._fetch_odds(match)
            message = (
                f"💰 PARIS | Dernière chance | {match.away_team} @ {match.home_team}\n"
                f"📊 xG | cotes actualisées\n"
                f"✅ +EV PICKS | {odds_line}"
            )
            await self.queue.enqueue(NHL_LIVE_CHANNEL_ID, message)
            await self.db.execute(
                "UPDATE matches SET notified_2h = 1 WHERE match_id = ?",
                (match.match_id,),
            )

    async def _process_injuries(self) -> None:
        injuries = await self._fetch_injuries()
        for entry in injuries:
            player = entry.get("player_name") or entry.get("player")
            team = entry.get("team") or "N/A"
            status = entry.get("status") or "OUT"
            impact = entry.get("impact") or "Impact inconnu"
            if not player:
                continue
            if NHL_KEY_PLAYERS and player.lower() not in NHL_KEY_PLAYERS:
                continue
            row = await self.db.fetchone(
                "SELECT injury_id, notified FROM injuries WHERE player_name = ? AND status = ?",
                (player, status),
            )
            if row and row["notified"]:
                continue
            await self.db.execute(
                """
                INSERT INTO injuries (player_name, team, status, impact, notified)
                VALUES (?, ?, ?, ?, 1)
                """,
                (player, team, status, impact),
            )
            message = f"⚠️ BLESSURE | {player} | {impact}"
            await self.queue.enqueue(NHL_LIVE_CHANNEL_ID, message)

    async def _run_match_updates(self) -> None:
        games = await self._fetch_schedule()
        for match in games:
            row = await self._get_match_row(match.match_id)
            await self._upsert_match(match)
            if match.status in {"In Progress", "Final"}:
                await self._announce_live_game(match, row)
                continue
            if match.status in {"Scheduled", "Pre-Game"}:
                if not (row and row["notified_upcoming"]):
                    message = (
                        f"⏰ À venir | {match.away_team} @ {match.home_team} | "
                        f"{match.start_time.astimezone(timezone.utc).strftime('%H:%M UTC')}"
                    )
                    await self.queue.enqueue(NHL_LIVE_CHANNEL_ID, message)
                    await self.db.execute(
                        "UPDATE matches SET notified_upcoming = 1 WHERE match_id = ?",
                        (match.match_id,),
                    )
                await self._maybe_send_preview(match, row)
                await self._maybe_send_last_call(match, row)

    @tasks.loop(minutes=15)
    async def live_score_task(self) -> None:
        try:
            await self._run_match_updates()
        except Exception as exc:
            logger.error("Failed to fetch schedule: %s", exc)
            await self._send_admin_alert(f"API NHL indisponible: {exc}")
            return

    @tasks.loop(minutes=15)
    async def betting_alerts_task(self) -> None:
        try:
            await self._run_match_updates()
        except Exception as exc:
            logger.error("Failed to fetch schedule for betting: %s", exc)
            await self._send_admin_alert(f"Betting alert schedule error: {exc}")
            return

    @tasks.loop(hours=1)
    async def injury_task(self) -> None:
        try:
            await self._process_injuries()
        except Exception as exc:
            logger.error("Failed to process injuries: %s", exc)
            await self._send_admin_alert(f"Injury feed error: {exc}")

    @live_score_task.before_loop
    @betting_alerts_task.before_loop
    @injury_task.before_loop
    async def before_tasks(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="status", description="Etat du système NHL notifications")
    async def status(self, interaction: discord.Interaction) -> None:
        queue_size = self.queue.queue.qsize()
        now = datetime.now(timezone.utc)
        message = (
            "📊 NHL Notifications Status\n"
            f"Queue: {queue_size} messages\n"
            f"Last check: {now.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="test_notification",
        description="Envoyer un test de notification NHL",
    )
    async def test_notification(self, interaction: discord.Interaction) -> None:
        await self.queue.enqueue(
            NHL_LIVE_CHANNEL_ID,
            "🔴 LIVE | TOR @ BOS | 3-2",
        )
        await interaction.response.send_message("Notification envoyée.", ephemeral=True)

    @app_commands.command(
        name="majnhl",
        description="Forcer une mise à jour manuelle des données NHL",
    )
    async def majnhl(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self._run_match_updates()
            await self._process_injuries()
        except Exception as exc:
            logger.error("Manual NHL update failed: %s", exc)
            await self._send_admin_alert(f"Mise à jour NHL manuelle en erreur: {exc}")
            await interaction.followup.send(
                "❌ Mise à jour manuelle échouée.", ephemeral=True
            )
            return
        await interaction.followup.send("✅ Mise à jour NHL terminée.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NHLNotificationsCog(bot))
