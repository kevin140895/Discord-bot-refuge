from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.refuge_journal import RefugeJournalIssue, refuge_journal_service
from storage.refuge_journal_store import refuge_journal_store
from ui.refuge_journal_view import RefugeJournalView
from utils.timezones import PARIS_TZ


logger = logging.getLogger(__name__)

try:
    REFUGE_JOURNAL_CHANNEL_ID = int(
        os.getenv("REFUGE_JOURNAL_CHANNEL_ID", "1400552164979507263")
    )
except ValueError:
    REFUGE_JOURNAL_CHANNEL_ID = 1400552164979507263


class RefugeJournalCog(commands.Cog):
    """Build and publish the weekly Journal du Refuge."""

    journal = app_commands.Group(
        name="journal",
        description="Administration du Journal du Refuge",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            created = await refuge_journal_service.ensure_baseline()
            if created:
                logger.info("[Journal] baseline initial créé")
        except Exception:
            logger.exception("[Journal] initialisation du baseline échouée")
        if not self.journal_scheduler.is_running():
            self.journal_scheduler.start()

    def cog_unload(self) -> None:
        self.journal_scheduler.cancel()

    async def _journal_channel(self) -> discord.TextChannel | discord.Thread | None:
        if REFUGE_JOURNAL_CHANNEL_ID <= 0:
            logger.warning("[Journal] REFUGE_JOURNAL_CHANNEL_ID invalide")
            return None
        channel = self.bot.get_channel(REFUGE_JOURNAL_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(REFUGE_JOURNAL_CHANNEL_ID)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                logger.exception("[Journal] salon de publication inaccessible")
                return None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning("[Journal] le salon configuré n'est pas textuel")
            return None
        return channel

    async def _build_issue(self, *, at: datetime | None = None) -> RefugeJournalIssue | None:
        guild_id: int | None = None
        channel = self.bot.get_channel(REFUGE_JOURNAL_CHANNEL_ID)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            guild_id = channel.guild.id
        elif len(self.bot.guilds) == 1:
            guild_id = self.bot.guilds[0].id
        return await refuge_journal_service.build_issue(at=at, guild_id=guild_id)

    async def _publish(self, *, at: datetime | None = None) -> tuple[bool, str]:
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        issue = await self._build_issue(at=now)
        if issue is None:
            return False, "Le baseline vient d'être créé ; aucune période fiable n'est encore publiable."

        if await refuge_journal_store.was_published(issue.publication_key):
            return False, f"Le numéro `{issue.publication_key}` a déjà été publié."

        channel = await self._journal_channel()
        if channel is None:
            return False, "Le salon du Journal est inaccessible ou mal configuré."

        try:
            message = await channel.send(view=RefugeJournalView(issue))
        except discord.HTTPException:
            logger.exception("[Journal] publication Discord échouée")
            return False, "Discord a refusé la publication ; le baseline n'a pas été avancé."

        try:
            await refuge_journal_store.commit_publication(
                publication_key=issue.publication_key,
                issue_number=issue.issue_number,
                message_id=message.id,
                published_at=now,
                period_start=issue.period_start,
                period_end=issue.period_end,
                users=issue.users_snapshot,
            )
        except Exception:
            logger.exception("[Journal] publication envoyée mais persistance échouée")
            try:
                await message.delete()
            except discord.HTTPException:
                logger.exception("[Journal] suppression du message non commité échouée")
            return False, "La publication n'a pas pu être validée dans le stockage."

        logger.info(
            "[Journal] numéro #%s publié dans %s (%s)",
            issue.issue_number,
            REFUGE_JOURNAL_CHANNEL_ID,
            issue.publication_key,
        )
        return True, f"Journal #{issue.issue_number} publié (`{issue.publication_key}`)."

    @tasks.loop(minutes=1)
    async def journal_scheduler(self) -> None:
        now = datetime.now(PARIS_TZ)
        if now.weekday() != 6 or now.hour < 20:
            return
        try:
            success, detail = await self._publish(at=now)
            if not success and "déjà été publié" not in detail:
                logger.info("[Journal] publication automatique ignorée: %s", detail)
        except Exception:
            logger.exception("[Journal] erreur pendant la publication automatique")

    @journal_scheduler.before_loop
    async def before_journal_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    @journal.command(name="preview", description="Prévisualiser le prochain Journal")
    @app_commands.checks.has_permissions(administrator=True)
    async def journal_preview(self, interaction: discord.Interaction) -> None:
        try:
            issue = await self._build_issue()
        except Exception:
            logger.exception("[Journal] génération de l'aperçu échouée")
            await interaction.response.send_message(
                "Impossible de générer l'aperçu du Journal.",
                ephemeral=True,
            )
            return
        if issue is None:
            await interaction.response.send_message(
                "Le baseline vient d'être initialisé. Un aperçu fiable sera disponible après de l'activité.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=RefugeJournalView(issue, preview=True),
            ephemeral=True,
        )

    @journal.command(name="publier", description="Publier immédiatement le Journal")
    @app_commands.checks.has_permissions(administrator=True)
    async def journal_publish(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            success, detail = await self._publish()
        except Exception:
            logger.exception("[Journal] publication manuelle échouée")
            await interaction.followup.send(
                "Impossible de publier le Journal.",
                ephemeral=True,
            )
            return
        prefix = "✅" if success else "ℹ️"
        await interaction.followup.send(f"{prefix} {detail}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugeJournalCog(bot))


__all__ = ["RefugeJournalCog", "REFUGE_JOURNAL_CHANNEL_ID"]
