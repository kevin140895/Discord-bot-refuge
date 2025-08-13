import os
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands
from zoneinfo import ZoneInfo

from utils.timewin import is_open_now, next_boundary_dt
from storage.roulette_store import RouletteStore

PARIS_TZ = "Europe/Paris"

# ✅ Tes IDs
ROLE_ID: int = 1405170057792979025          # Rôle temporaire pour le jackpot 500 XP
CHANNEL_ID: int = 1405170020748755034       # Salon où poster la roulette

# Tirage pondéré
REWARDS = [0, 5, 50, 500]
WEIGHTS = [40, 40, 18, 2]  # total 100

def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

class RouletteView(discord.ui.View):
    """Vue persistante avec le bouton 🎰 Roulette."""
    def __init__(self, *, enabled: bool):
        super().__init__(timeout=None)
        # on initialise disabled en fonction de enabled
        try:
            self.play_button.disabled = not enabled  # type: ignore[attr-defined]
        except Exception:
            pass

    @discord.ui.button(
        label="🎰 Roulette",
        style=discord.ButtonStyle.success,
        custom_id="roulette:play"
    )
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Optional["RouletteCog"] = interaction.client.get_cog("RouletteCog")  # type: ignore
        if not cog:
            return await interaction.response.send_message("❌ Fonction Roulette indisponible.", ephemeral=True)

        # 1) Fenêtre horaire
        if not is_open_now(PARIS_TZ, 10, 22):
            nxt = next_boundary_dt(tz=PARIS_TZ, start_h=10, end_h=22)
            return await interaction.response.send_message(
                f"⏳ La roulette est ouverte **de 10:00 à 22:00 (Europe/Paris)**.\n"
                f"🔔 Prochaine ouverture/fermeture : **{_fmt(nxt)}**.",
                ephemeral=True
            )

        # 2) Une seule tentative / jour
        uid = str(interaction.user.id)
        if cog.store.has_claimed_today(uid, tz=PARIS_TZ):
            now = datetime.now(cog.tz)
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            rest = int((tomorrow - now).total_seconds() // 60)
            h, m = divmod(rest, 60)
            return await interaction.response.send_message(
                f"🗓️ Tu as déjà joué **aujourd’hui**.\n"
                f"⏳ Tu pourras rejouer dans **{h}h{m:02d}** (après minuit).",
                ephemeral=True
            )

        # 3) Tirage
        gain = random.choices(REWARDS, weights=WEIGHTS, k=1)[0]

        # 4) Attribution XP via l'API exposée par le bot
        try:
            old_lvl, new_lvl, total_xp = await cog.bot.award_xp(interaction.user.id, gain)  # type: ignore[attr-defined]
        except Exception as e:
            logging.exception("[Roulette] award_xp a échoué: %s", e)
            return await interaction.response.send_message("❌ Erreur interne (XP). Réessaie plus tard.", ephemeral=True)

        # 5) Rôle 24h UNIQUEMENT si 500 XP
        role_given = False
        expires_at_txt = None
        if gain == 500 and ROLE_ID and interaction.guild:
            guild = interaction.guild
            role = guild.get_role(ROLE_ID)
            me = guild.me or guild.get_member(cog.bot.user.id)  # type: ignore
            if not role:
                logging.warning("[Roulette] ROLE_ID introuvable.")
            elif not me or not guild.me.guild_permissions.manage_roles:
                logging.warning("[Roulette] Permission 'Gérer les rôles' manquante.")
            else:
                try:
                    if role >= me.top_role:
                        logging.warning("[Roulette] Rôle au-dessus (ou égal) du rôle du bot — attribution impossible.")
                    else:
                        await interaction.user.add_roles(role, reason="Roulette (gagnant 500 XP)")
                        role_given = True
                        expires_at = datetime.now(cog.tz) + timedelta(hours=24)
                        expires_at_txt = _fmt(expires_at)
                        cog.store.upsert_role_assignment(
                            user_id=uid,
                            guild_id=str(guild.id),
                            role_id=str(role.id),
                            expires_at=expires_at.isoformat()
                        )
                except discord.Forbidden:
                    logging.warning("[Roulette] Forbid: impossible d'ajouter le rôle.")
                except Exception as e:
                    logging.error("[Roulette] add_roles échec: %s", e)

        # 6) Marque l'utilisateur comme ayant joué aujourd'hui
        cog.store.mark_claimed_today(uid, tz=PARIS_TZ)

        # 7) Message de résultat
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
                msg += f"\n🎖️ Tu reçois le rôle temporaire pendant **24h** (jusqu’au **{expires_at_txt}**)."

        # Annonce level-up si besoin (si ton main expose bot.announce_level_up)
        try:
            announce = getattr(cog.bot, "announce_level_up", None)
            if announce and new_lvl > old_lvl:
                await announce(interaction.guild, interaction.user, old_lvl, new_lvl, total_xp)
        except Exception as e:
            logging.error("[Roulette] announce_level_up échouée: %s", e)

        await interaction.response.send_message(msg, ephemeral=True)


class RouletteCog(commands.Cog):
    """Roulette : horaires, tirage, XP, rôle 24h, persistance quotidienne, commandes d’admin."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tz = ZoneInfo(PARIS_TZ)
        # 🔒 Force le stockage sur /data (volume Railway)
        data_dir = "/data"
        self.store = RouletteStore(data_dir=data_dir)

        # État initial du bouton selon l’heure
        self.current_view_enabled = is_open_now(PARIS_TZ, 10, 22)

    # ——— UI helpers ———
    def _build_view(self) -> RouletteView:
        """Construit une nouvelle view avec bouton activé/désactivé selon la fenêtre horaire."""
        return RouletteView(enabled=self.current_view_enabled)

    def _poster_embed(self) -> discord.Embed:
        desc_state = (
            "✅ **Ouverte** de 10:00 à 22:00 (Europe/Paris)"
            if self.current_view_enabled
            else "⛔ **Fermée** (10:00–22:00)"
        )
        return discord.Embed(
            title="🎰 Roulette",
            description=(
                f"{desc_state}\n\n"
                "Clique pour tenter ta chance : 0 / 5 / 50 / **500** XP.\n"
                "✨ **Le rôle 24h est attribué uniquement si tu gagnes 500 XP.**\n"
                "🗓️ **Une seule tentative par jour.**"
            ),
            color=0x2ECC71 if self.current_view_enabled else 0xED4245
        )

    async def _refresh_poster_message(self):
        """Réédite le message posté (s’il existe) pour refléter l’état du bouton."""
        poster = self.store.get_poster()
        if not poster:
            return
        channel_id = int(poster.get("channel_id", 0))
        message_id = int(poster.get("message_id", 0))
        ch = self.bot.get_channel(channel_id)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return
        try:
            msg = await ch.fetch_message(message_id)
        except Exception:
            return

        try:
            await msg.edit(embed=self._poster_embed(), view=self._build_view())
        except Exception as e:
            logging.error("[Roulette] Échec edit poster: %s", e)

    # ——— Tâches ———
    @tasks.loop(minutes=3.0)
    async def roles_cleanup_loop(self):
        """Toutes les 3 minutes: retirer les rôles expirés (robuste au redémarrage)."""
        try:
            now = datetime.now(self.tz)
            assignments = self.store.get_all_role_assignments()
            for uid, data in list(assignments.items()):
                try:
                    exp = datetime.fromisoformat(data.get("expires_at"))
                except Exception:
                    self.store.clear_role_assignment(uid)
                    continue

                if now >= exp:
                    guild_id = int(data.get("guild_id", 0))
                    role_id = int(data.get("role_id", 0))
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        self.store.clear_role_assignment(uid)
                        continue
                    member = guild.get_member(int(uid))
                    role = guild.get_role(role_id)
                    if member and role:
                        try:
                            await member.remove_roles(role, reason="Roulette: expiration 24h")
                        except Exception as e:
                            logging.warning("[Roulette] remove_roles échec pour %s: %s", uid, e)
                    self.store.clear_role_assignment(uid)
        except Exception as e:
            logging.error("[Roulette] roles_cleanup_loop erreur: %s", e)

    @tasks.loop(seconds=60.0)
    async def boundary_watch_loop(self):
        """
        Surveille la fenêtre horaire : si on franchit 10:00/22:00,
        (dés)active le bouton et met à jour le message.
        """
        try:
            enabled_now = is_open_now(PARIS_TZ, 10, 22)
            if enabled_now != self.current_view_enabled:
                self.current_view_enabled = enabled_now
                await self._refresh_poster_message()
        except Exception as e:
            logging.error("[Roulette] boundary_watch_loop erreur: %s", e)

    # ——— Lifecycle ———
    async def cog_load(self):
        # Enregistrer la view persistante correspondant à l’état courant
        try:
            self.bot.add_view(self._build_view())
        except Exception as e:
            logging.error("[Roulette] add_view échoué: %s", e)

        # Lancer les tâches
        self.roles_cleanup_loop.start()
        self.boundary_watch_loop.start()

        # Sweep initial
        try:
            await self._refresh_poster_message()
        except Exception:
            pass

    async def cog_unload(self):
        try:
            self.roles_cleanup_loop.cancel()
        except Exception:
            pass
        try:
            self.boundary_watch_loop.cancel()
        except Exception:
            pass

    # ——— Commandes ———
    @app_commands.command(name="roulette-poster", description="Publie le message Roulette avec le bouton")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roulette_poster(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        ch = interaction.guild.get_channel(CHANNEL_ID) if CHANNEL_ID else interaction.channel
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return await interaction.followup.send("❌ Salon cible introuvable.", ephemeral=True)

        try:
            msg = await ch.send(embed=self._poster_embed(), view=self._build_view())
            self.store.set_poster(channel_id=str(ch.id), message_id=str(msg.id))
            await interaction.followup.send(f"✅ Message posté dans <#{ch.id}>.", ephemeral=True)
        except Exception as e:
            logging.error("[Roulette] Poster échoué: %s", e)
            await interaction.followup.send("❌ Impossible de poster le message.", ephemeral=True)

    @app_commands.command(name="roulette-refresh", description="Réédite le message Roulette (texte + état bouton)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roulette_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        poster = self.store.get_poster()
        if not poster:
            return await interaction.followup.send("❌ Aucun message enregistré. Utilise d’abord `/roulette-poster`.", ephemeral=True)
        await self._refresh_poster_message()
        await interaction.followup.send("🔁 Message Roulette réédité.", ephemeral=True)

    @app_commands.command(name="roulette-reset-user", description="Réinitialise l’état Roulette d’un membre")
    @app_commands.describe(membre="Membre à réinitialiser (permet de rejouer)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roulette_reset_user(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer(ephemeral=True)

        uid = str(membre.id)
        # 1) Enlever la marque du jour
        self.store.unmark_claimed(uid)

        # 2) S'il a une attribution de rôle en cours, on la supprime et on retire le rôle
        data = self.store.get_role_assignment(uid)
        if data:
            guild_id = int(data.get("guild_id", 0))
            role_id = int(data.get("role_id", 0))
            if interaction.guild and interaction.guild.id == guild_id:
                role = interaction.guild.get_role(role_id)
                if role and role in membre.roles:
                    try:
                        await membre.remove_roles(role, reason="Roulette reset user")
                    except Exception as e:
                        logging.warning("[Roulette] remove_roles (reset) échec: %s", e)
            self.store.clear_role_assignment(uid)

        await interaction.followup.send(f"♻️ **{membre.display_name}** peut rejouer **aujourd’hui**.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteCog(bot))
