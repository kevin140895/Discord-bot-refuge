from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ANNOUNCE_CHANNEL_ID
from storage.community_goal_store import ACTIVE_STATUS, community_goal_store
from storage.season_store import season_store
from utils.community_goals import (
    COMMUNITY_GOAL_METRICS,
    COMMUNITY_GOAL_METRICS_BY_KEY,
    aggregate_metric_total,
    format_goal_value,
    goal_progress,
    progress_bar,
    progress_percent,
)


logger = logging.getLogger(__name__)


class CommunityGoalsCog(commands.Cog):
    """Staff-created collective objectives backed by seasonal activity data."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.evaluate_community_goals.start()

    def cog_unload(self) -> None:
        self.evaluate_community_goals.cancel()

    async def _metric_total(self, field: str) -> int:
        season_ids = await season_store.list_seasons()
        payloads: list[dict[str, Any]] = []
        for season_id in season_ids:
            payload = await season_store.get_season(season_id)
            if isinstance(payload, dict):
                payloads.append(payload)
        return aggregate_metric_total(payloads, field)

    async def _goal_progress(self, goal: dict[str, Any]) -> int:
        metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(str(goal.get("metric_key", "")))
        if metric is None:
            return 0
        current_total = await self._metric_total(metric.season_field)
        return goal_progress(
            current_total,
            int(goal.get("baseline_total", 0)),
            int(goal.get("target", 0)),
        )

    async def _announce_completion(
        self,
        goal: dict[str, Any],
        progress: int,
    ) -> None:
        if ANNOUNCE_CHANNEL_ID <= 0:
            return
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ANNOUNCE_CHANNEL_ID)
            except discord.HTTPException:
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(str(goal.get("metric_key", "")))
        if metric is None:
            return
        title = str(goal.get("title") or metric.label)
        target = int(goal.get("target", 0))
        reward = goal.get("reward_text")
        description = (
            f"🎉 La communauté a atteint **{title}** !\n"
            f"{metric.emoji} {format_goal_value(metric.key, progress)} / "
            f"{format_goal_value(metric.key, target)}"
        )
        if reward:
            description += f"\n🎁 Récompense prévue : **{reward}**"
        embed = discord.Embed(title="🏆 Objectif communautaire atteint", description=description)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.warning("Impossible d'annoncer l'objectif communautaire atteint")

    async def _evaluate_goals(self) -> None:
        active_goals = await community_goal_store.list_goals(status=ACTIVE_STATUS)
        if not active_goals:
            return

        now = datetime.now(timezone.utc)
        totals: dict[str, int] = {}
        for goal in active_goals:
            metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(str(goal.get("metric_key", "")))
            if metric is None:
                continue
            if metric.season_field not in totals:
                totals[metric.season_field] = await self._metric_total(metric.season_field)

            progress = goal_progress(
                totals[metric.season_field],
                int(goal.get("baseline_total", 0)),
                int(goal.get("target", 0)),
            )
            try:
                ends_at = datetime.fromisoformat(str(goal.get("ends_at")))
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                ends_at = now

            if now >= ends_at:
                await community_goal_store.finish_goal(
                    str(goal["id"]),
                    status="expired",
                    final_progress=progress,
                    at=now,
                )
                continue

            if progress >= int(goal.get("target", 0)):
                finished = await community_goal_store.finish_goal(
                    str(goal["id"]),
                    status="completed",
                    final_progress=progress,
                    at=now,
                )
                if finished is not None:
                    await self._announce_completion(finished, progress)

    @tasks.loop(minutes=1)
    async def evaluate_community_goals(self) -> None:
        try:
            await self._evaluate_goals()
        except Exception:
            logger.exception("Erreur pendant l'évaluation des objectifs communautaires")

    @evaluate_community_goals.before_loop
    async def before_evaluate_community_goals(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="objectif_communaute",
        description="Affiche les objectifs communautaires en cours",
    )
    async def objectif_communaute(self, interaction: discord.Interaction) -> None:
        await self._evaluate_goals()
        active_goals = await community_goal_store.list_goals(status=ACTIVE_STATUS)
        completed = await community_goal_store.list_goals(status="completed")

        embed = discord.Embed(title="🎯 Objectifs communautaires")
        if not active_goals:
            embed.description = "Aucun objectif communautaire actif pour le moment."
        else:
            for goal in active_goals:
                metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(
                    str(goal.get("metric_key", ""))
                )
                if metric is None:
                    continue
                progress = await self._goal_progress(goal)
                target = int(goal.get("target", 0))
                percent = progress_percent(progress, target)
                try:
                    ends_at = datetime.fromisoformat(str(goal.get("ends_at")))
                    end_timestamp = int(ends_at.timestamp())
                    deadline = f"<t:{end_timestamp}:R>"
                except (TypeError, ValueError):
                    deadline = "échéance inconnue"

                title = str(goal.get("title") or metric.label)
                value = (
                    f"{progress_bar(progress, target)} **{percent}%**\n"
                    f"{metric.emoji} {format_goal_value(metric.key, progress)} / "
                    f"{format_goal_value(metric.key, target)}\n"
                    f"⏳ Fin {deadline}"
                )
                if goal.get("reward_text"):
                    value += f"\n🎁 Récompense prévue : {goal['reward_text']}"
                embed.add_field(name=title, value=value, inline=False)

        if completed:
            recent = completed[:3]
            lines = []
            for goal in recent:
                metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(
                    str(goal.get("metric_key", ""))
                )
                if metric is None:
                    continue
                title = str(goal.get("title") or metric.label)
                lines.append(f"✅ {title}")
            if lines:
                embed.add_field(
                    name="Derniers objectifs réussis",
                    value="\n".join(lines),
                    inline=False,
                )

        embed.set_footer(text="Progression calculée depuis la création de chaque objectif")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="objectif_creer",
        description="Crée un objectif communautaire",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        categorie="Type de progression collective",
        cible="Valeur à atteindre (heures pour le vocal)",
        duree_jours="Durée de l'objectif en jours",
        titre="Titre personnalisé facultatif",
        recompense="Récompense prévue, affichée sans distribution automatique",
    )
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name=metric.label, value=metric.key)
            for metric in COMMUNITY_GOAL_METRICS
        ]
    )
    async def objectif_creer(
        self,
        interaction: discord.Interaction,
        categorie: app_commands.Choice[str],
        cible: app_commands.Range[int, 1, 10_000_000],
        duree_jours: app_commands.Range[int, 1, 90],
        titre: str | None = None,
        recompense: str | None = None,
    ) -> None:
        await self._evaluate_goals()
        metric = COMMUNITY_GOAL_METRICS_BY_KEY.get(categorie.value)
        if metric is None:
            await interaction.response.send_message(
                "Catégorie d'objectif inconnue.", ephemeral=True
            )
            return

        target = metric.to_base_value(int(cible))
        baseline = await self._metric_total(metric.season_field)
        now = datetime.now(timezone.utc)
        try:
            goal = await community_goal_store.create_goal(
                metric_key=metric.key,
                target=target,
                baseline_total=baseline,
                created_by=interaction.user.id,
                created_at=now,
                ends_at=now + timedelta(days=int(duree_jours)),
                title=titre,
                reward_text=recompense,
            )
        except ValueError as exc:
            if "already exists" in str(exc):
                message = "Un objectif actif existe déjà pour cette catégorie."
            else:
                message = "Impossible de créer cet objectif."
            await interaction.response.send_message(message, ephemeral=True)
            return

        end_timestamp = int(datetime.fromisoformat(str(goal["ends_at"])).timestamp())
        await interaction.response.send_message(
            (
                f"✅ Objectif créé : **{goal.get('title') or metric.label}**\n"
                f"Cible : **{format_goal_value(metric.key, target)}**\n"
                f"Fin : <t:{end_timestamp}:F>"
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="objectif_annuler",
        description="Annule l'objectif actif d'une catégorie",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name=metric.label, value=metric.key)
            for metric in COMMUNITY_GOAL_METRICS
        ]
    )
    async def objectif_annuler(
        self,
        interaction: discord.Interaction,
        categorie: app_commands.Choice[str],
    ) -> None:
        await self._evaluate_goals()
        active_goals = await community_goal_store.list_goals(status=ACTIVE_STATUS)
        goal = next(
            (
                item
                for item in active_goals
                if item.get("metric_key") == categorie.value
            ),
            None,
        )
        if goal is None:
            await interaction.response.send_message(
                "Aucun objectif actif dans cette catégorie.", ephemeral=True
            )
            return
        progress = await self._goal_progress(goal)
        await community_goal_store.finish_goal(
            str(goal["id"]),
            status="cancelled",
            final_progress=progress,
        )
        await interaction.response.send_message("Objectif annulé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityGoalsCog(bot))
