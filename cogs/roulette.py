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
from .xp import award_xp
from config import (
    ANNOUNCE_CHANNEL_ID,
    ROLE_NOTIFICATION as NOTIF_ROLE_ID,
    ROULETTE_ROLE_ID as ROLE_ID,
    ROULETTE_CHANNEL_ID as CHANNEL_ID,
    DATA_DIR,
    ROULETTE_BOUNDARY_CHECK_INTERVAL_MINUTES,
)

PARIS_TZ = "Europe/Paris"
WINNER_ROLE_NAME = "🏆 Gagnant Roulette"
REWARDS = [0, 5, 50, 500]
WEIGHTS = [40, 40, 18, 2]
SPIN_GIF_URL = "https://media.tenor.com/ZzOaGh2sg2AAAAAi/roulette-spin.gif"
WIN_GIF_URL = "https://media.tenor.com/XwI-iYdkfVIAAAAi/lottery-winner.gif"

def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class RouletteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎰 Roulette",
        style=discord.ButtonStyle.success,
        custom_id="roulette:play",
    )
    async def play_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog: Optional["RouletteCog"] = interaction.client.get_cog(
            "RouletteCog",
        )  # type: ignore
        if not cog:
            return await interaction.response.send_message(
                "❌ Fonction Roulette indisponible.",
                ephemeral=True,
            )

        if not is_open_now(PARIS_TZ, 10, 22):
            nxt = next_boundary_dt(tz=PARIS_TZ, start_h=10, end_h=22)
            return await interaction.response.send_message(
                (
                    "⏳ La roulette est ouverte "
                    "**de 10:00 à 22:00 (Europe/Paris)**.\n"
                    f"🔔 Prochaine ouverture/fermeture : **{_fmt(nxt)}**."
                ),
                ephemeral=True,
            )

        uid = str(interaction.user.id)
        if cog.store.has_claimed_today(uid, tz=PARIS_TZ):
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
                ephemeral=True
            )

        gain = random.choices(REWARDS, weights=WEIGHTS, k=1)[0]
        try:
            old_lvl, new_lvl, total_xp = await award_xp(
                interaction.user.id,
                gain,
            )
        except Exception as e:
            logging.exception("[Roulette] award_xp a échoué: %s", e)
            return await interaction.response.send_message(
                "❌ Erreur interne (XP). Réessaie plus tard.",
                ephemeral=True,
            )

        role_given = False
        expires_at_txt = None
        if gain == 500 and ROLE_ID and interaction.guild:
            guild = interaction.guild
            role = guild.get_role(ROLE_ID)
            me = guild.me or guild.get_member(cog.bot.user.id)  # type: ignore
            if role and me and me.guild_permissions.manage_roles:
                try:
                    if role < me.top_role:
                        await interaction.user.add_roles(
                            role,
                            reason="Roulette (gagnant 500 XP)",
                        )
                        role_given = True
                        expires_at = (
                            datetime.now(cog.tz) + timedelta(hours=24)
                        )
                        expires_at_txt = _fmt(expires_at)
                        cog.store.upsert_role_assignment(
                            user_id=uid,
                            guild_id=str(guild.id),
                            role_id=str(role.id),
                            expires_at=expires_at.isoformat()
                        )
                except Exception as e:
                    logging.error("[Roulette] add_roles échec: %s", e)
        cog.store.mark_claimed_today(uid, tz=PARIS_TZ)
        msg = f"🎰 Résultat : **{gain} XP**."
        if gain == 0:
            msg += "\n😅 Pas de chance cette fois…"
        elif gain == 5:
            msg += "\n🔹 Un petit bonus, c'est toujours ça !"
        elif gain == 50:
            msg += "\n🔸 Beau tirage !"
        else:
            msg += "\n💎 **Jackpot !**"
            if role_given and expires_at_txt:
                msg += (
                    "\n🎖️ Tu reçois le rôle "
                    f"**{WINNER_ROLE_NAME}** pendant **24h** "
                    f"(jusqu’au **{expires_at_txt}**)."
                )

            ch = cog.bot.get_channel(ANNOUNCE_CHANNEL_ID)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    embed = discord.Embed(
                        title="🎉 Jackpot !",
                        description=(
                            f"{interaction.user.mention} a gagné **500 XP** à la roulette !"
                        ),
                        color=0xFFD700,
                    )
                    embed.set_image(url=WIN_GIF_URL)
                    await ch.send(embed=embed)
                except Exception as e:
                    logging.error("[Roulette] Échec annonce gagnant: %s", e)

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
            logging.error("[Roulette] announce_level_up échouée: %s", e)

        await interaction.response.defer(ephemeral=True)
        spin_embed = discord.Embed(title="🎰 La roulette tourne…")
        spin_embed.set_image(url=SPIN_GIF_URL)
        spin_msg = await interaction.followup.send(
            embed=spin_embed,
            ephemeral=True,
        )
        await asyncio.sleep(5)
        await spin_msg.edit(content=msg, embed=None)


class RouletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tz = ZoneInfo(PARIS_TZ)
        self.store = RouletteStore(data_dir=DATA_DIR)
        self.current_view_enabled = is_open_now(PARIS_TZ, 10, 22)
        self._last_announced_state: Optional[bool] = None

    def _poster_embed(self) -> discord.Embed:
        if self.current_view_enabled:
            desc_state = "✅ **Ouverte** de 10:00 à 22:00 (Europe/Paris)"
            color = 0x2ECC71
        else:
            desc_state = "⛔ **Fermée** (10:00–22:00)"
            color = 0xED4245
        return discord.Embed(
            title="🎰 Roulette",
            description=(
                f"{desc_state}\n\n"
                "Clique pour tenter ta chance : 0 / 5 / 50 / **500** XP.\n"
                f"✨ Le rôle **{WINNER_ROLE_NAME}** est attribué pendant "
                "**24h** si tu gagnes **500 XP**.\n"
                "🗓️ **Une seule tentative par jour.**"
            ),
            color=color
        )

    async def _delete_old_poster_message(self):
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
            logging.debug("Failed to delete old poster message: %s", e)
        self.store.clear_poster()

    async def _replace_poster_message(self):
        await self.bot.wait_until_ready()
        await self._delete_old_poster_message()
        ch = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logging.warning("[Roulette] Salon roulette introuvable.")
            return
        try:
            if self.current_view_enabled:
                msg = await ch.send(
                    embed=self._poster_embed(),
                    view=RouletteView(),
                )
            else:
                msg = await ch.send(embed=self._poster_embed())
            self.store.set_poster(
                channel_id=str(ch.id),
                message_id=str(msg.id),
            )
            logging.info("[Roulette] Nouveau message roulette publié.")
        except Exception as e:
            logging.error(
                f"[Roulette] Échec envoi nouveau message roulette: {e}"
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
                    and msg.embeds[0].title == "🎰 Roulette"
                ):
                    return msg
        except Exception as e:
            logging.debug("Failed to find existing poster: %s", e)
        return None

    async def _ensure_poster_message(self):
        poster = self.store.get_poster()
        if poster:
            ch = self.bot.get_channel(int(poster.get("channel_id", 0)))
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    await ch.fetch_message(int(poster.get("message_id", 0)))
                    return
                except discord.NotFound as e:
                    logging.debug("Poster message missing: %s", e)
        existing = await self._find_existing_poster()
        if existing:
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
        except Exception as err:
            logging.warning("[Roulette] Init failed: %s", err)
        self.maintenance_loop.start()

    async def _post_state_message(self, opened: bool):
        ch = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logging.warning("[Roulette] ANNOUNCE_CHANNEL_ID invalide.")
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
                        logging.debug("Failed to fetch old state message: %s", e)
            if not msg_to_delete:
                try:
                    async for m in ch.history(limit=20):
                        if (
                            m.author.id == self.bot.user.id
                            and m.embeds
                            and m.embeds[0].title.startswith("🎰 Roulette —")
                        ):
                            msg_to_delete = m
                            break
                except Exception as e:
                    logging.debug("Error scanning history for state msg: %s", e)
            if msg_to_delete:
                try:
                    await msg_to_delete.delete()
                except Exception as e:
                    logging.debug("Failed to delete old state msg: %s", e)

            content = None
            allowed = None
            if opened:
                content = (
                    f"<@&{NOTIF_ROLE_ID}> 🎰 La **roulette ouvre** maintenant "
                    "— vous pouvez jouer jusqu’à **22:00**."
                )
                allowed = discord.AllowedMentions(roles=True)
            embed = discord.Embed(
                title=f"🎰 Roulette — {'OUVERTE' if opened else 'FERMÉE'}",
                description=(
                    "✅ La roulette est **ouverte** de **10:00 à 22:00** "
                    "(Europe/Paris)." if opened else
                    "⛔ La roulette est **fermée**. "
                    "Rendez-vous **demain à 10:00** (Europe/Paris) !"
                ),
                color=0x2ECC71 if opened else 0xED4245,
            )
            msg = await ch.send(
                content=content,
                embed=embed,
                allowed_mentions=allowed,
            )
            self.store.set_state_message(str(ch.id), str(msg.id))
        except Exception as e:
            logging.error("[Roulette] Post state message fail: %s", e)

    async def _ensure_state_message(self, opened: bool):
        ch = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logging.warning("[Roulette] ANNOUNCE_CHANNEL_ID invalide.")
            return
        stored = self.store.get_state_message()
        if stored:
            try:
                msg = await ch.fetch_message(int(stored.get("message_id", 0)))
                if (
                    msg.embeds
                    and msg.embeds[0].title
                    == f"🎰 Roulette — {'OUVERTE' if opened else 'FERMÉE'}"
                ):
                    return
            except discord.NotFound as e:
                logging.debug("State message missing: %s", e)
        try:
            async for msg in ch.history(limit=20):
                if (
                    msg.author.id == self.bot.user.id
                    and msg.embeds
                    and msg.embeds[0].title
                    == f"🎰 Roulette — {'OUVERTE' if opened else 'FERMÉE'}"
                ):
                    self.store.set_state_message(str(ch.id), str(msg.id))
                    return
        except Exception as e:
            logging.debug("Error ensuring state message: %s", e)
        await self._post_state_message(opened)

    @tasks.loop(minutes=ROULETTE_BOUNDARY_CHECK_INTERVAL_MINUTES)
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
                await self._post_state_message(enabled_now)
                self._last_announced_state = enabled_now
        except Exception as e:
            logging.error("[Roulette] maintenance_loop boundary erreur: %s", e)

        # Surveillance du message de la roulette
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
            logging.error(f"[Roulette] maintenance_loop poster erreur: {e}")

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
                                await member.remove_roles(role, reason="Roulette rôle expiré")
                            except Exception as e:
                                logging.error("[Roulette] maintenance_loop remove_roles erreur: %s", e)
                    self.store.clear_role_assignment(uid)
        except Exception as e:
            logging.error(f"[Roulette] maintenance_loop roles erreur: {e}")

    @maintenance_loop.before_loop
    async def before_maintenance_loop(self):
        await self.bot.wait_until_ready()

    # ── Slash command admin ──
    group = app_commands.Group(
        name="roulette",
        description="Gestion de la roulette",
    )

    @group.command(
        name="refresh",
        description="Republier le message de la roulette",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh_roulette(self, interaction: discord.Interaction):
        with measure("slash:roulette_refresh"):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._replace_poster_message()
            await interaction.followup.send("✅ Message roulette rafraîchi.", ephemeral=True)

    async def cog_load(self):
        try:
            self.bot.add_view(RouletteView())
        except Exception as e:
            logging.error("[Roulette] add_view échoué: %s", e)
        self.bot.loop.create_task(self._init_after_ready())

    async def cog_unload(self):
        self.maintenance_loop.cancel()

async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteCog(bot))
