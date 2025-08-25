import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

from utils.timewin import is_open_now, next_boundary_dt
from utils.metrics import measure
from storage.roulette_store import RouletteStore
from ..xp import award_xp, add_xp_boost
from config import (
    ANNOUNCE_CHANNEL_ID,
    ROLE_NOTIFICATION as NOTIF_ROLE_ID,
    MACHINE_A_SOUS_ROLE_ID as ROLE_ID,
    MACHINE_A_SOUS_CHANNEL_ID as CHANNEL_ID,
    DATA_DIR,
    MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES,
)
from utils.discord_utils import safe_message_edit
logger = logging.getLogger(__name__)

PARIS_TZ = "Europe/Paris"
WINNER_ROLE_NAME = "🏆 Gagnant Machine à sous"
# Répartition des gains (poids total = 1000)
REWARDS = [
    0,
    5,
    20,
    50,
    100,
    500,
    1000,
    "ticket",
    "double_xp",
    "shared_xp",
]
WEIGHTS = [250, 230, 150, 80, 40, 15, 5, 80, 80, 70]
SPIN_GIF_URL = "https://media.tenor.com/2roX3zvclxkAAAAC/slot-machine.gif"
WIN_GIF_URL = "https://media.tenor.com/XwI-iYdkfVIAAAAi/lottery-winner.gif"

def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class MachineASousView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _reward_ticket(
        self,
        interaction: discord.Interaction,
        cog: "MachineASousCog",
        free: bool,
    ):
        """Handle the ticket reward which grants an extra spin."""
        if not free:
            cog.store.mark_claimed_today(str(interaction.user.id), tz=PARIS_TZ)
        msg = "🎟️ Ticket gratuit ! Tu peux rejouer immédiatement."
        return msg, False, None, 0, 0, 0, 0

    async def _reward_double_xp(
        self,
        interaction: discord.Interaction,
        cog: "MachineASousCog",
        free: bool,
    ):
        """Activate a temporary double XP boost."""
        if not free:
            cog.store.mark_claimed_today(str(interaction.user.id), tz=PARIS_TZ)
        add_xp_boost(interaction.user.id, 60)
        msg = "⚡ Double XP activé pour toi pendant 1h !"
        return msg, False, None, 0, 0, 0, 0

    async def _reward_shared_xp(
        self,
        interaction: discord.Interaction,
        cog: "MachineASousCog",
        free: bool,
    ):
        """Award XP to the user and a random person in a voice channel."""
        if not free:
            cog.store.mark_claimed_today(str(interaction.user.id), tz=PARIS_TZ)
        other = None
        if interaction.guild:
            pool = [
                m
                for vc in interaction.guild.voice_channels
                for m in vc.members
                if not m.bot and m.id != interaction.user.id
            ]
            if pool:
                other = random.choice(pool)
        try:
            old_lvl, new_lvl, old_xp, total_xp = await award_xp(
                interaction.user.id,
                50,
                guild_id=interaction.guild_id,
                source="machine_a_sous",
            )
        except Exception as e:
            logger.exception("[MachineASous] award_xp a échoué: %s", e)
            await interaction.followup.send(
                "❌ Erreur interne (XP). Réessaie plus tard.",
                ephemeral=True,
            )
            return None
        if other:
            try:
                await award_xp(
                    other.id, 50, guild_id=interaction.guild_id, source="machine_a_sous"
                )
            except Exception as e:
                logger.exception("[MachineASous] award_xp (shared) échec: %s", e)
        if other:
            msg = f"🤝 XP partagé ! Toi et {other.mention} gagnez chacun 50 XP."
        else:
            msg = "🤝 XP partagé… mais personne en vocal. Tu gagnes 50 XP !"
        return msg, False, None, old_lvl, new_lvl, old_xp, total_xp

    async def _reward_xp_gain(
        self,
        interaction: discord.Interaction,
        cog: "MachineASousCog",
        gain: int,
        free: bool,
    ):
        """Handle classic XP rewards, including jackpots and roles."""
        uid = str(interaction.user.id)
        role_given = False
        expires_at_txt = None
        try:
            old_lvl, new_lvl, old_xp, total_xp = await award_xp(
                interaction.user.id,
                gain,
                guild_id=interaction.guild_id,
                source="machine_a_sous",
            )
        except Exception as e:
            logger.exception("[MachineASous] award_xp a échoué: %s", e)
            await interaction.followup.send(
                "❌ Erreur interne (XP). Réessaie plus tard.",
                ephemeral=True,
            )
            return None

        if gain == 1000 and ROLE_ID and interaction.guild:
            guild = interaction.guild
            role = guild.get_role(ROLE_ID)
            me = guild.me or guild.get_member(cog.bot.user.id)  # type: ignore
            if role and me and me.guild_permissions.manage_roles:
                try:
                    if role < me.top_role:
                        await interaction.user.add_roles(
                            role, reason="Machine à sous (gagnant 1000 XP)"
                        )
                        role_given = True
                        expires_at = datetime.now(cog.tz) + timedelta(hours=24)
                        expires_at_txt = _fmt(expires_at)
                        cog.store.upsert_role_assignment(
                            user_id=uid,
                            guild_id=str(guild.id),
                            role_id=str(role.id),
                            expires_at=expires_at.isoformat(),
                        )
                except Exception as e:
                    logger.error("[MachineASous] add_roles échec: %s", e)
        if not free:
            cog.store.mark_claimed_today(uid, tz=PARIS_TZ)

        msg = f"🎰 Résultat : **{gain} XP**."
        if gain == 0:
            msg += "\n😅 Pas de chance cette fois…"
        elif gain == 5:
            msg += "\n🔹 Un petit bonus, c'est toujours ça !"
        elif gain == 20:
            msg += "\n🎯 Pas mal !"
        elif gain == 50:
            msg += "\n🔸 Beau tirage !"
        elif gain == 100:
            msg += "\n🎉 Super gain !"
        elif gain == 500:
            msg += "\n💰 **Jackpot intermédiaire !**"
        else:  # 1000
            msg += "\n💎 **Super Jackpot !**"
            if role_given and expires_at_txt:
                msg += (
                    "\n🎖️ Tu reçois le rôle "
                    f"**{WINNER_ROLE_NAME}** pendant **24h** "
                    f"(jusqu’au **{expires_at_txt}**)."
                )

        if gain >= 500:
            ch = cog.bot.get_channel(ANNOUNCE_CHANNEL_ID)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    embed = discord.Embed(
                        title="🎉 Jackpot !",
                        description=(
                            f"{interaction.user.mention} a gagné **{gain} XP** à la machine à sous !"
                        ),
                        color=0xFFD700,
                    )
                    embed.set_image(url=WIN_GIF_URL)
                    await ch.send(embed=embed)
                except Exception as e:
                    logger.error(
                        "[MachineASous] Échec annonce gagnant: %s", e
                    )

        return msg, role_given, expires_at_txt, old_lvl, new_lvl, old_xp, total_xp

    async def _single_spin(
        self,
        interaction: discord.Interaction,
        cog: "MachineASousCog",
        free: bool = False,
    ) -> None:
        if free:
            choices = [r for r in REWARDS if r != 0]
            weights = [w for r, w in zip(REWARDS, WEIGHTS) if r != 0]
            gain = random.choices(choices, weights=weights, k=1)[0]
        else:
            gain = random.choices(REWARDS, weights=WEIGHTS, k=1)[0]
        if gain == "ticket":
            result = await self._reward_ticket(interaction, cog, free)
        elif gain == "double_xp":
            result = await self._reward_double_xp(interaction, cog, free)
        elif gain == "shared_xp":
            result = await self._reward_shared_xp(interaction, cog, free)
            if result is None:
                return
        else:
            result = await self._reward_xp_gain(interaction, cog, gain, free)
            if result is None:
                return

        msg, _, _, old_lvl, new_lvl, _, total_xp = result

        try:
            announce = getattr(cog.bot, "announce_level_up", None)
            if announce and new_lvl > old_lvl:
                await announce(
                    interaction.guild,
                    interaction.user,
                    old_lvl,
                    new_lvl,
                    total_xp,
                )
        except Exception as e:
            logger.error("[MachineASous] announce_level_up échouée: %s", e)

        spin_embed = discord.Embed(title="🎰 La machine à sous tourne…")
        spin_embed.set_image(url=SPIN_GIF_URL)
        spin_msg = await interaction.followup.send(
            embed=spin_embed,
            ephemeral=True,
        )
        await asyncio.sleep(5)
        await safe_message_edit(spin_msg, content=msg, embed=None)

        if gain == "ticket":
            await self._single_spin(interaction, cog, free=True)

    @discord.ui.button(
        label="🎰 Machine à sous",
        style=discord.ButtonStyle.success,
        custom_id="machineasous:play",
    )
    async def play_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog: Optional["MachineASousCog"] = interaction.client.get_cog(
            "MachineASousCog",
        )  # type: ignore
        if not cog:
            return await interaction.response.send_message(
                "❌ Fonction Machine à sous indisponible.",
                ephemeral=True,
            )

        if not is_open_now(PARIS_TZ, 10, 22):
            nxt = next_boundary_dt(tz=PARIS_TZ, start_h=10, end_h=22)
            return await interaction.response.send_message(
                (
                    "⏳ La machine à sous est ouverte "
                    "**de 10:00 à 22:00 (Europe/Paris)**.\n"
                    f"🔔 Prochaine ouverture/fermeture : **{_fmt(nxt)}**."
                ),
                ephemeral=True,
            )

        uid = str(interaction.user.id)
        has_claimed = cog.store.has_claimed_today(uid, tz=PARIS_TZ)

        # Utilise d'abord un ticket disponible, même si l'utilisateur n'a pas
        # encore effectué son tirage quotidien. Cela permet d'utiliser un
        # ticket « en réserve » sans consommer l'essai journalier.
        if cog.store.use_ticket(uid):
            await interaction.response.defer(ephemeral=True)
            await self._single_spin(interaction, cog, free=True)
            return

        if has_claimed:
            now = datetime.now(cog.tz)
            tomorrow = (
                now + timedelta(days=1)
            ).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            rest = int((tomorrow - now).total_seconds() // 60)
            h, m = divmod(rest, 60)
            return await interaction.response.send_message(
                f"🗓️ Tu as déjà joué **aujourd’hui**.\n"
                f"⏳ Tu pourras rejouer dans **{h}h{m:02d}** (après minuit).",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await self._single_spin(interaction, cog)


class MachineASousCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tz = ZoneInfo(PARIS_TZ)
        self.store = RouletteStore(data_dir=DATA_DIR)
        self.current_view_enabled = is_open_now(PARIS_TZ, 10, 22)
        self._last_announced_state: Optional[bool] = None

    def _poster_embed(self) -> discord.Embed:
        """Create the poster embed describing rewards and open state."""
        if self.current_view_enabled:
            desc_state = "✅ **Ouverte** de 10:00 à 22:00 (Europe/Paris)"
            color = 0x2ECC71
        else:
            desc_state = "⛔ **Fermée** (10:00–22:00)"
            color = 0xED4245
        return discord.Embed(
            title="🎰 Machine à sous",
            description=(
                f"{desc_state}\n\n"
                "0 / 5 / 20 / 50 / 100 / 500 / **1000** XP\n"
                "🎟️ Ticket gratuit • ⚡ Double XP (1h) • 🤝 XP partagé\n"
                f"✨ Le rôle **{WINNER_ROLE_NAME}** est attribué pendant "
                "**24h** si tu gagnes le **Super Jackpot**.\n"
                "🗓️ **Une seule tentative par jour.**"
            ),
            color=color,
        )

    async def _delete_old_poster_message(self):
        """Delete the previously stored poster message if present."""
        poster = self.store.get_poster()
        if not poster:
            return
        ch = self.bot.get_channel(int(poster.get("channel_id", 0)))
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            self.store.clear_poster()
            return
        try:
            msg = await ch.fetch_message(int(poster.get("message_id", 0)))
            await msg.delete()
        except Exception as e:
            logger.debug("Failed to delete old poster message: %s", e)
        self.store.clear_poster()

    async def _replace_poster_message(self):
        """Publish a fresh poster message in the slot machine channel."""
        await self.bot.wait_until_ready()
        await self._delete_old_poster_message()
        ch = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logger.warning("[MachineASous] Salon machine à sous introuvable.")
            return
        try:
            if self.current_view_enabled:
                msg = await ch.send(
                    embed=self._poster_embed(),
                    view=MachineASousView(),
                )
            else:
                msg = await ch.send(embed=self._poster_embed())
            self.store.set_poster(
                channel_id=str(ch.id),
                message_id=str(msg.id),
            )
            logger.info("[MachineASous] Nouveau message machine à sous publié.")
        except Exception as e:
            logger.error(
                f"[MachineASous] Échec envoi nouveau message machine à sous: {e}"
            )

    async def _find_existing_poster(self) -> Optional[discord.Message]:
        ch = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return None
        try:
            async for msg in ch.history(limit=20):
                if (
                    msg.author.id == self.bot.user.id
                    and msg.embeds
                    and msg.embeds[0].title == "🎰 Machine à sous"
                ):
                    return msg
        except Exception as e:
            logger.debug("Failed to find existing poster: %s", e)
        return None

    async def _ensure_poster_message(self):
        poster = self.store.get_poster()
        if poster:
            stored_ch_id = int(poster.get("channel_id", 0))
            if stored_ch_id != CHANNEL_ID:
                # L'ID configuré a changé : supprimer l'ancien message
                await self._delete_old_poster_message()
            else:
                ch = self.bot.get_channel(stored_ch_id)
                if isinstance(ch, (discord.TextChannel, discord.Thread)):
                    try:
                        msg = await ch.fetch_message(int(poster.get("message_id", 0)))
                        has_view = bool(msg.components)
                        if has_view != self.current_view_enabled:
                            await self._replace_poster_message()
                        return
                    except discord.NotFound as e:
                        logger.debug("Poster message missing: %s", e)
        existing = await self._find_existing_poster()
        if existing:
            if bool(existing.components) != self.current_view_enabled:
                await self._replace_poster_message()
            else:
                self.store.set_poster(
                    channel_id=str(existing.channel.id),
                    message_id=str(existing.id),
                )
        else:
            await self._replace_poster_message()

    async def _init_after_ready(self):
        await self.bot.wait_until_ready()
        self.current_view_enabled = is_open_now(PARIS_TZ, 10, 22)
        self._last_announced_state = self.current_view_enabled
        try:
            await self._ensure_poster_message()
            await self._ensure_state_message(self.current_view_enabled)
        except Exception as err:
            logger.warning("[MachineASous] Init failed: %s", err)
        self.maintenance_loop.start()

    async def _post_state_message(self, opened: bool):
        """Announce the open/closed state in the announcement channel."""
        ch = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logger.warning("[MachineASous] ANNOUNCE_CHANNEL_ID invalide.")
            return
        try:
            old = self.store.get_state_message()
            msg_to_delete = None
            if old:
                old_ch = self.bot.get_channel(int(old.get("channel_id", 0)))
                if isinstance(old_ch, (discord.TextChannel, discord.Thread)):
                    try:
                        msg_to_delete = await old_ch.fetch_message(
                            int(old.get("message_id", 0))
                        )
                    except Exception as e:
                        logger.debug("Failed to fetch old state message: %s", e)
            if not msg_to_delete:
                try:
                    async for m in ch.history(limit=20):
                        if (
                            m.author.id == self.bot.user.id
                            and m.embeds
                            and m.embeds[0].title.startswith("🎰 Machine à sous —")
                        ):
                            msg_to_delete = m
                            break
                except Exception as e:
                    logger.debug("Error scanning history for state msg: %s", e)
            if msg_to_delete:
                try:
                    await msg_to_delete.delete()
                except Exception as e:
                    logger.debug("Failed to delete old state msg: %s", e)

            content = None
            allowed = None
            title = f"🎰 Machine à sous — {'OUVERTE' if opened else 'FERMÉE'}"
            if opened:
                content = (
                    f"<@&{NOTIF_ROLE_ID}> 🎰 La **machine à sous ouvre** maintenant — vous pouvez jouer jusqu’à **22:00**."
                )
                allowed = discord.AllowedMentions(roles=True)
                description = (
                    "Place tes mises et laisse tourner la roue... qui sait où elle s’arrêtera ?\n\n"
                    "💎 Super Jackpot → +1000 XP (ultra rare – 0,1% de chance !)\n"
                    "⚡ Double XP (1h) → booste tes gains pendant 1h chrono !\n"
                    "🎟️ Ticket gratuit → un tirage offert par la maison.\n"
                    "🤝 XP partagé → toi + un joueur aléatoire en vocal gagnez chacun +50 XP.\n\n"
                    "🎯 Gains classiques :\n"
                    "0️⃣ Perdu… la maison gagne 💀\n"
                    "5️⃣ Petit lot – 5 XP 🪙\n"
                    "2️⃣0️⃣ Bonus – 20 XP 🎯\n"
                    "5️⃣0️⃣ Gain sympa – 50 XP 💵\n"
                    "1️⃣0️⃣0️⃣ Belle prise – 100 XP 💸\n"
                    "5️⃣0️⃣0️⃣ JACKPOT intermédiaire – 500 XP 💰\n\n"
                    "🏆 Gagnant Machine à sous est attribué pendant 24h si tu gagnes le **Super Jackpot**\n\n"
                    "Bonne chance, et que la roue tourne en ta faveur !"
                )
                color = 0x2ECC71
            else:
                content = (
                    f"<@&{NOTIF_ROLE_ID}> 🎰 La **machine à sous ferme** maintenant — rendez-vous demain !"
                )
                allowed = discord.AllowedMentions(roles=True)
                description = (
                    "💡 Les néons s’éteignent… ⛔\n"
                    "À demain pour de nouvelles mises et, peut-être, le gros lot 💰."
                )
                color = 0xED4245
            embed = discord.Embed(title=title, description=description, color=color)
            msg = await ch.send(
                content=content,
                embed=embed,
                allowed_mentions=allowed,
            )
            self.store.set_state_message(str(ch.id), str(msg.id))
        except Exception as e:
            logger.error("[MachineASous] Post state message fail: %s", e)

    async def _ensure_state_message(self, opened: bool):
        """Ensure a state message exists and matches the current status."""
        ch = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logger.warning("[MachineASous] ANNOUNCE_CHANNEL_ID invalide.")
            return
        stored = self.store.get_state_message()
        if stored:
            try:
                msg = await ch.fetch_message(int(stored.get("message_id", 0)))
                if (
                    msg.embeds
                    and msg.embeds[0].title
                    == f"🎰 Machine à sous — {'OUVERTE' if opened else 'FERMÉE'}"
                ):
                    return
            except discord.NotFound as e:
                logger.debug("State message missing: %s", e)
        try:
            async for msg in ch.history(limit=20):
                if (
                    msg.author.id == self.bot.user.id
                    and msg.embeds
                    and msg.embeds[0].title
                    == f"🎰 Machine à sous — {'OUVERTE' if opened else 'FERMÉE'}"
                ):
                    self.store.set_state_message(str(ch.id), str(msg.id))
                    return
        except Exception as e:
            logger.debug("Error ensuring state message: %s", e)
        await self._post_state_message(opened)

    @tasks.loop(minutes=MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES)
    async def maintenance_loop(self):
        # Vérification des horaires d'ouverture
        try:
            enabled_now = is_open_now(PARIS_TZ, 10, 22)
            if (
                self._last_announced_state is None
                or enabled_now != self._last_announced_state
            ):
                self.current_view_enabled = enabled_now
                await self._replace_poster_message()
                self._last_announced_state = enabled_now
            await self._ensure_state_message(enabled_now)
        except Exception as e:
            logger.error("[MachineASous] maintenance_loop boundary erreur: %s", e)

        # Surveillance du message de la machine à sous
        try:
            poster = self.store.get_poster()
            if not poster:
                await self._replace_poster_message()
            else:
                ch = self.bot.get_channel(int(poster.get("channel_id", 0)))
                if not isinstance(ch, (discord.TextChannel, discord.Thread)):
                    await self._replace_poster_message()
                else:
                    try:
                        await ch.fetch_message(int(poster.get("message_id", 0)))
                    except discord.NotFound:
                        await self._replace_poster_message()
        except Exception as e:
            logger.error(f"[MachineASous] maintenance_loop poster erreur: {e}")

        # Nettoyage des rôles temporaires
        try:
            assignments = self.store.get_all_role_assignments()
            now = datetime.now(self.tz)
            for uid, data in list(assignments.items()):
                try:
                    exp = datetime.fromisoformat(data.get("expires_at", "")).astimezone(self.tz)
                except Exception:
                    self.store.clear_role_assignment(uid)
                    continue
                if exp <= now:
                    guild = self.bot.get_guild(int(data.get("guild_id", 0)))
                    if guild:
                        member = guild.get_member(int(uid))
                        role = guild.get_role(int(data.get("role_id", 0)))
                        if member and role:
                            try:
                                await member.remove_roles(role, reason="Machine à sous rôle expiré")
                            except Exception as e:
                                logger.error("[MachineASous] maintenance_loop remove_roles erreur: %s", e)
                    self.store.clear_role_assignment(uid)
        except Exception as e:
            logger.error(f"[MachineASous] maintenance_loop roles erreur: {e}")

    @maintenance_loop.before_loop
    async def before_maintenance_loop(self):
        await self.bot.wait_until_ready()

    # ── Slash command admin ──
    group = app_commands.Group(
        name="machine",
        description="Gestion de la machine à sous",
    )

    @group.command(
        name="refresh",
        description="Republier le message de la machine à sous",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh_machine(self, interaction: discord.Interaction):
        with measure("slash:machine_refresh"):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._replace_poster_message()
            await interaction.followup.send("✅ Message machine à sous rafraîchi.", ephemeral=True)

    async def cog_load(self):
        try:
            self.bot.add_view(MachineASousView())
        except Exception as e:
            logger.error("[MachineASous] add_view échoué: %s", e)
        asyncio.create_task(self._init_after_ready())

    async def cog_unload(self):
        self.maintenance_loop.cancel()

async def setup(bot: commands.Bot):
    await bot.add_cog(MachineASousCog(bot))
