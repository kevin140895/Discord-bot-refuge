from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DATA_DIR
from storage.season_store import season_store
from ui.season_leaderboard_view import (
    SeasonLeaderboardEntry,
    SeasonLeaderboardView,
)
from utils.persistence import read_json_safe
from utils.seasons import (
    SEASON_METRICS,
    SEASON_METRICS_BY_KEY,
    format_metric_value,
    parse_season_id,
    rank_rows,
    season_id_for,
    season_label,
    split_interval_by_season,
)


CASINO_STATE_FILE = Path(DATA_DIR) / "pari_xp_state.json"


def _casino_players_from_raw(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    players = raw.get("players", {})
    if not isinstance(players, dict):
        return {}
    return {
        str(user_id): dict(payload)
        for user_id, payload in players.items()
        if isinstance(payload, dict)
    }


class SeasonalLeaderboardsCog(commands.Cog):
    """Monthly activity leaderboards that never reset global bot data."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._voice_sessions: dict[int, datetime] = {}

    async def _sync_casino(self) -> None:
        raw = await asyncio.to_thread(read_json_safe, CASINO_STATE_FILE, {})
        await season_store.sync_casino_totals(_casino_players_from_raw(raw))

    async def cog_load(self) -> None:
        await season_store.ensure_tracking_started()
        # First observation is baseline-only: no pre-feature casino history is
        # injected into the current season.
        await self._sync_casino()
        await season_store.flush()
        self.flush_season_stats.start()

    def cog_unload(self) -> None:
        self.flush_season_stats.cancel()
        try:
            asyncio.create_task(season_store.flush())
        except RuntimeError:
            pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Start prospective voice timing for members already connected."""

        now = datetime.now(timezone.utc)
        active_ids: set[int] = set()
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    active_ids.add(member.id)
                    self._voice_sessions.setdefault(member.id, now)

        for user_id in list(self._voice_sessions):
            if user_id not in active_ids:
                self._voice_sessions.pop(user_id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        await season_store.record(
            message.author.id,
            at=message.created_at,
            messages=1,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or before.channel == after.channel:
            return

        now = datetime.now(timezone.utc)
        if before.channel is not None:
            started_at = self._voice_sessions.pop(member.id, None)
            if started_at is not None:
                for season_id, seconds in split_interval_by_season(started_at, now):
                    await season_store.record(
                        member.id,
                        season_id=season_id,
                        at=now,
                        voice_seconds=seconds,
                    )

        if after.channel is not None:
            self._voice_sessions[member.id] = now

    @tasks.loop(seconds=30)
    async def flush_season_stats(self) -> None:
        await self._sync_casino()
        await season_store.flush()

    @flush_season_stats.before_loop
    async def before_flush_season_stats(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="classement_saison",
        description="Affiche le classement mensuel du Refuge",
    )
    @app_commands.describe(
        categorie="Classement à afficher",
        saison="Mois historique au format AAAA-MM (mois actuel par défaut)",
    )
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name=metric.label, value=metric.key)
            for metric in SEASON_METRICS
        ]
    )
    async def classement_saison(
        self,
        interaction: discord.Interaction,
        categorie: app_commands.Choice[str],
        saison: str | None = None,
    ) -> None:
        metric = SEASON_METRICS_BY_KEY.get(categorie.value)
        if metric is None:
            await interaction.response.send_message(
                "Catégorie saisonnière inconnue.", ephemeral=True
            )
            return

        season_id = saison or season_id_for()
        try:
            parse_season_id(season_id)
        except ValueError:
            await interaction.response.send_message(
                "Format de saison invalide. Utilise `AAAA-MM`, par exemple `2026-08`.",
                ephemeral=True,
            )
            return

        payload = await season_store.get_season(season_id)
        if payload is None:
            available = await season_store.list_seasons()
            suffix = (
                " Saisons disponibles : " + ", ".join(available[:6]) + "."
                if available
                else " Le suivi commencera avec la première activité enregistrée."
            )
            await interaction.response.send_message(
                f"Aucune donnée pour {season_label(season_id)}.{suffix}",
                ephemeral=True,
            )
            return

        users = payload.get("users", {})
        if not isinstance(users, dict):
            users = {}
        rows = rank_rows(users, metric.field)

        visible_rows: list[tuple[str, int, str]] = []
        for user_id, value in rows:
            display = f"<@{user_id}>"
            if interaction.guild is not None:
                try:
                    member = interaction.guild.get_member(int(user_id))
                except (TypeError, ValueError):
                    member = None
                if member is not None:
                    if member.bot:
                        continue
                    display = member.display_name
            visible_rows.append((user_id, value, display))
            if len(visible_rows) >= 10:
                break

        entries = tuple(
            SeasonLeaderboardEntry(
                rank=rank,
                display_name=display,
                value=format_metric_value(metric.key, value),
            )
            for rank, (_user_id, value, display) in enumerate(
                visible_rows,
                start=1,
            )
        )

        started_at = payload.get("started_at")
        tracking_note: str | None = None
        if started_at:
            try:
                started = datetime.fromisoformat(str(started_at))
                tracking_note = (
                    f"Suivi de cette saison depuis <t:{int(started.timestamp())}:d>."
                )
            except ValueError:
                pass

        await interaction.response.send_message(
            view=SeasonLeaderboardView(
                metric_label=metric.label,
                season_label_text=season_label(season_id),
                entries=entries,
                tracking_note=tracking_note,
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SeasonalLeaderboardsCog(bot))
