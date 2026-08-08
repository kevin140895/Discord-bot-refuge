from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DATA_DIR
from storage.achievement_store import achievement_store
from storage.xp_store import xp_store
from utils.achievements import (
    ACHIEVEMENTS,
    CATEGORY_LABELS,
    achievement_progress,
    qualifying_achievement_ids,
)
from utils.persistence import read_json_safe


logger = logging.getLogger(__name__)
CASINO_STATE_FILE = Path(DATA_DIR) / "pari_xp_state.json"


def _load_casino_players() -> dict[str, dict[str, Any]]:
    raw = read_json_safe(CASINO_STATE_FILE, {})
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


def _member_metrics(
    member: discord.Member,
    xp_snapshot: Mapping[str, Mapping[str, Any]],
    casino_players: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Build achievement metrics only from existing authoritative data."""

    uid = str(member.id)
    xp_payload = xp_snapshot.get(uid, {})
    casino_payload = casino_players.get(uid, {})

    try:
        level = int(xp_payload.get("level", 0))
    except (TypeError, ValueError):
        level = 0
    try:
        casino_bets = int(casino_payload.get("bets", 0))
    except (TypeError, ValueError):
        casino_bets = 0

    tenure_days = 0
    joined_at = member.joined_at
    if joined_at is not None:
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        tenure_days = max(0, (current - joined_at.astimezone(timezone.utc)).days)

    return {
        "level": max(0, level),
        "casino_bets": max(0, casino_bets),
        "tenure_days": tenure_days,
    }


def _format_progress(metric: str, current: int, target: int) -> str:
    if metric == "level":
        return f"niveau {current}/{target}"
    if metric == "casino_bets":
        return f"{current}/{target} paris"
    if metric == "tenure_days":
        return f"{current}/{target} jours"
    return f"{current}/{target}"


class AchievementsCog(commands.Cog):
    """Persistent achievements derived from existing XP, casino and tenure data."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.achievement_sync.start()

    def cog_unload(self) -> None:
        self.achievement_sync.cancel()

    async def _xp_snapshot(self) -> dict[str, dict[str, Any]]:
        async with xp_store.lock:
            return {
                str(user_id): dict(payload)
                for user_id, payload in xp_store.data.items()
                if isinstance(payload, dict)
            }

    async def _sync_member(
        self,
        member: discord.Member,
        casino_players: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[dict[str, int], list[str]]:
        xp_snapshot = await self._xp_snapshot()
        players = casino_players if casino_players is not None else _load_casino_players()
        metrics = _member_metrics(member, xp_snapshot, players)
        newly_unlocked = await achievement_store.unlock_many(
            member.id,
            qualifying_achievement_ids(metrics),
        )
        return metrics, newly_unlocked

    @tasks.loop(minutes=15)
    async def achievement_sync(self) -> None:
        """Recognize newly reached badges without requiring the slash command."""

        xp_snapshot = await self._xp_snapshot()
        casino_players = _load_casino_players()
        current = datetime.now(timezone.utc)
        unlocks: dict[int, list[str]] = {}

        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                metrics = _member_metrics(
                    member,
                    xp_snapshot,
                    casino_players,
                    now=current,
                )
                qualified = qualifying_achievement_ids(metrics)
                if qualified:
                    unlocks[member.id] = qualified

        if unlocks:
            await achievement_store.unlock_batch(unlocks, unlocked_at=current)

    @achievement_sync.before_loop
    async def before_achievement_sync(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="succes",
        description="Affiche les succès et badges d'un membre",
    )
    @app_commands.describe(membre="Membre à consulter (toi par défaut)")
    async def succes(
        self,
        interaction: discord.Interaction,
        membre: discord.Member | None = None,
    ) -> None:
        target = membre
        if target is None and isinstance(interaction.user, discord.Member):
            target = interaction.user
        if target is None and interaction.guild is not None:
            target = interaction.guild.get_member(interaction.user.id)
        if target is None:
            await interaction.response.send_message(
                "Impossible de déterminer le membre à afficher.",
                ephemeral=True,
            )
            return

        metrics, newly_unlocked = await self._sync_member(target)
        unlocked = await achievement_store.get_user_achievements(target.id)

        embed = discord.Embed(
            title=f"🏅 Succès de {target.display_name}",
            description=(
                f"**{len(unlocked)}/{len(ACHIEVEMENTS)}** badges débloqués."
            ),
        )
        if newly_unlocked:
            embed.description += (
                f"\n✨ **{len(newly_unlocked)} nouveau(x) succès** reconnu(s) maintenant."
            )

        for category, label in CATEGORY_LABELS.items():
            lines: list[str] = []
            for achievement in ACHIEVEMENTS:
                if achievement.category != category:
                    continue
                if achievement.id in unlocked:
                    lines.append(
                        f"✅ {achievement.emoji} **{achievement.name}** — {achievement.description}"
                    )
                else:
                    current, target_value = achievement_progress(achievement, metrics)
                    progress = _format_progress(
                        achievement.metric,
                        current,
                        target_value,
                    )
                    lines.append(
                        f"🔒 {achievement.emoji} **{achievement.name}** — {progress}"
                    )
            embed.add_field(name=label, value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AchievementsCog(bot))
