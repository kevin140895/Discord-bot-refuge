
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
ANNOUNCE_CHANNEL_ID = 1400552164979507263
NOTIF_ROLE_ID = 1404882154370109450
WINNER_ROLE_NAME = "🏆 Gagnant Roulette"
ROLE_ID = 1405170057792979025
CHANNEL_ID = 1405170020748755034
REWARDS = [0, 5, 50, 500]
WEIGHTS = [40, 40, 18, 2]

def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

class RouletteView(discord.ui.View):
    def __init__(self, *, enabled: bool):
        super().__init__(timeout=None)
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

        if not is_open_now(PARIS_TZ, 10, 22):
            nxt = next_boundary_dt(tz=PARIS_TZ, start_h=10, end_h=22)
            return await interaction.response.send_message(
                f"⏳ La roulette est ouverte **de 10:00 à 22:00 (Europe/Paris)**.\n"
                f"🔔 Prochaine ouverture/fermeture : **{_fmt(nxt)}**.",
                ephemeral=True
            )

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

        gain = random.choices(REWARDS, weights=WEIGHTS, k=1)[0]
        try:
            old_lvl, new_lvl, total_xp = await cog.bot.award_xp(interaction.user.id, gain)  # type: ignore[attr-defined]
        except Exception as e:
            logging.exception("[Roulette] award_xp a échoué: %s", e)
            return await interaction.response.send_message("❌ Erreur interne (XP). Réessaie plus tard.", ephemeral=True)

        role_given = False
        expires_at_txt = None
        if gain == 500 and ROLE_ID and interaction.guild:
            guild = interaction.guild
            role = guild.get_role(ROLE_ID)
            me = guild.me or guild.get_member(cog.bot.user.id)  # type: ignore
            if role and me and guild.me.guild_permissions.manage_roles:
                try:
                    if role < me.top_role:
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
                    f"\n🎖️ Tu reçois le rôle **{WINNER_ROLE_NAME}** pendant **24h** "
                    f"(jusqu’au **{expires_at_txt}**)."
                )

        try:
            announce = getattr(cog.bot, "announce_level_up", None)
            if announce and new_lvl > old_lvl:
                await announce(interaction.guild, interaction.user, old_lvl, new_lvl, total_xp)
        except Exception as e:
            logging.error("[Roulette] announce_level_up échouée: %s", e)

        await interaction.response.send_message(msg, ephemeral=True)

# The rest of the Cog implementation would go here (omitted for brevity in this output)
