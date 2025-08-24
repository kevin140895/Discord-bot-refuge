import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import TEMP_VC_CATEGORY
from utils.interactions import safe_respond
from view import RSVPView
from utils.game_events import (
    GameEvent,
    EVENTS,
    load_events,
    save_event,
    set_voice_channel,
)
logger = logging.getLogger(__name__)


class GameEventModal(discord.ui.Modal):
    """Modal de création d'un événement de jeu."""

    def __init__(self, cog: "GameEventsCog") -> None:
        super().__init__(title="Organiser un jeu")
        self.cog = cog
        self.game_type = discord.ui.TextInput(label="Type de jeu")
        self.game_name = discord.ui.TextInput(label="Nom du jeu")
        self.date_time = discord.ui.TextInput(
            label="Date & heure (JJ/MM/AAAA HH:MM)",
            placeholder="22/10/2025 20:30",
        )
        self.add_item(self.game_type)
        self.add_item(self.game_name)
        self.add_item(self.date_time)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog._create_event(
            interaction,
            self.game_type.value,
            self.game_name.value,
            self.date_time.value,
        )


class GameEventsCog(commands.Cog):
    """Gestion des événements de jeu organisés."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        load_events()
        for evt in EVENTS.values():
            if evt.state in {"scheduled", "waiting"}:
                try:
                    bot.add_view(RSVPView(evt.id), message_id=evt.message_id)
                except Exception:
                    logger.exception("[game] Impossible d'attacher la vue pour %s", evt.id)
        self.scheduler.start()

    def cog_unload(self) -> None:
        self.scheduler.cancel()

    @app_commands.command(name="jeu_organise", description="Organiser un événement de jeu")
    async def jeu_organise(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await safe_respond(
                interaction,
                "Commande utilisable uniquement sur un serveur.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(GameEventModal(self))

    async def _create_event(
        self,
        interaction: discord.Interaction,
        game_type: str,
        game_name: str,
        date_str: str,
    ) -> None:
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)
        except Exception:
            await safe_respond(
                interaction,
                "Format de date invalide (JJ/MM/AAAA HH:MM)",
                ephemeral=True,
            )
            return
        evt = GameEvent(
            id=uuid.uuid4().hex,
            guild_id=interaction.guild_id,
            creator_id=interaction.user.id,
            game_type=game_type,
            game_name=game_name,
            time=dt,
            channel_id=interaction.channel_id,
            message_id=0,
        )
        EVENTS[evt.id] = evt
        await save_event(evt)
        embed = discord.Embed(
            title=f"🎮 {game_name}",
            description=f"Type: {game_type}\nDébut: <t:{int(dt.timestamp())}:F>",
            color=discord.Color.blue(),
        )
        view = RSVPView(evt.id)
        channel = interaction.channel
        assert isinstance(channel, discord.abc.Messageable)
        msg = await channel.send(embed=embed, view=view)
        evt.message_id = msg.id
        await save_event(evt)
        self.bot.add_view(view, message_id=msg.id)
        await safe_respond(interaction, "Événement créé ✔️", ephemeral=True)
        logger.info("[game] Création événement %s", evt.id)

    # ---------- boucle de planification ----------

    @tasks.loop(seconds=30)
    async def scheduler(self) -> None:
        now = datetime.now(timezone.utc)
        for evt in list(EVENTS.values()):
            try:
                await self._process_event(evt, now)
            except Exception as e:
                logger.exception("[game] scheduler error for %s: %s", evt.id, e)

    async def _process_event(self, evt: GameEvent, now: datetime) -> None:
        guild = self.bot.get_guild(evt.guild_id)
        if guild is None:
            return
        if (
            evt.state == "scheduled"
            and not evt.reminder_sent
            and now >= evt.time - timedelta(hours=1)
        ):
            channel = guild.get_channel(evt.channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"Rappel: {evt.game_name} commence dans une heure !"
                )
            evt.reminder_sent = True
            await save_event(evt)
        # T-10 minutes: création salon vocal et DMs
        if evt.state == "scheduled" and now >= evt.time - timedelta(minutes=10):
            creator = guild.get_member(evt.creator_id)
            name = f"👥 {creator.display_name if creator else 'Joueur'}・{evt.game_name}"
            category = guild.get_channel(TEMP_VC_CATEGORY)
            try:
                vc = await guild.create_voice_channel(name, category=category)
            except discord.HTTPException as e:
                logger.error("[game] création salon échouée pour %s: %s", evt.id, e)
                return
            set_voice_channel(evt, vc.id)
            await save_event(evt)
            for uid, status in evt.rsvps.items():
                if status in {"yes", "maybe"}:
                    member = guild.get_member(int(uid))
                    if member:
                        try:
                            await member.send(
                                f"{evt.game_name} commence dans 10 minutes !"
                            )
                        except discord.HTTPException:
                            logger.info("[game] DM refusé pour %s", uid)
            evt.state = "waiting"
            await save_event(evt)
            logger.info("[game] Salon vocal créé pour %s", evt.id)
        # À l'heure H: annonce ou attente
        if evt.state in {"scheduled", "waiting"} and now >= evt.time:
            if any(s in {"yes", "maybe"} for s in evt.rsvps.values()):
                channel = guild.get_channel(evt.channel_id)
                vc = guild.get_channel(evt.voice_channel_id) if evt.voice_channel_id else None
                if isinstance(channel, discord.TextChannel):
                    yes_mentions = [f"<@{u}>" for u, s in evt.rsvps.items() if s == "yes"]
                    maybe_mentions = [f"<@{u}>" for u, s in evt.rsvps.items() if s == "maybe"]
                    desc = "\n".join(yes_mentions) or "Personne"
                    desc += "\nPeut-être : " + (", ".join(maybe_mentions) or "aucun")
                    msg = await channel.send(
                        f"C'est parti pour **{evt.game_name}** !\n"
                        f"Salon vocal: {vc.mention if vc else 'n/a'}\n"
                        f"Joueurs: {desc}\n"
                        "Multiplicateurs: ✅ x2, 🤔 x1.5, autres x1",
                    )
                    try:
                        await msg.pin()
                    except discord.HTTPException:
                        pass
                evt.started_at = now
                evt.state = "running"
                await save_event(evt)
                logger.info("[game] Annonce publiée pour %s", evt.id)
            else:
                if evt.state != "waiting":
                    evt.state = "waiting"
                    await save_event(evt)

        if (
            evt.state in {"scheduled", "waiting"}
            and now >= evt.time + timedelta(minutes=30)
        ):
            if not any(s in {"yes", "maybe"} for s in evt.rsvps.values()):
                vc = guild.get_channel(evt.voice_channel_id) if evt.voice_channel_id else None
                if not isinstance(vc, discord.VoiceChannel) or not vc.members:
                    creator = guild.get_member(evt.creator_id)
                    if creator:
                        try:
                            await creator.send(
                                f"Ton événement **{evt.game_name}** est annulé (aucun RSVP)."
                            )
                        except discord.HTTPException:
                            pass
                    if isinstance(vc, discord.VoiceChannel):
                        try:
                            await vc.delete(reason="Événement annulé")
                        except discord.HTTPException:
                            pass
                    set_voice_channel(evt, None)
                    evt.state = "cancelled"
                    await save_event(evt)
                    logger.info("[game] Événement %s annulé", evt.id)
        # Fin de session quand le vocal est vide
        if (
            evt.state == "running"
            and evt.voice_channel_id
            and evt.started_at
            and now - evt.started_at > timedelta(minutes=5)
        ):
            vc = guild.get_channel(evt.voice_channel_id)
            if not isinstance(vc, discord.VoiceChannel) or not vc.members:
                duration = int((now - (evt.started_at or now)).total_seconds() // 60)
                counts = {"x2": 0, "x1.5": 0, "x1": 0}
                for uid in evt.participants:
                    status = evt.rsvps.get(str(uid))
                    if status == "yes":
                        counts["x2"] += 1
                    elif status == "maybe":
                        counts["x1.5"] += 1
                    else:
                        counts["x1"] += 1
                channel = guild.get_channel(evt.channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"Session terminée : {evt.game_name}\n"
                        f"Durée : {duration} min\n"
                        f"Participants : {len(evt.participants)}\n"
                        f"Bonus appliqués : x2={counts['x2']}, x1.5={counts['x1.5']}, x1={counts['x1']}"
                    )
                if isinstance(vc, discord.VoiceChannel):
                    try:
                        await vc.delete(reason="Événement terminé")
                    except discord.HTTPException:
                        pass
                set_voice_channel(evt, None)
                evt.state = "finished"
                evt.ended_at = now
                await save_event(evt)
                logger.info("[game] Événement %s terminé", evt.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameEventsCog(bot))
