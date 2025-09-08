import logging

import discord
from discord.ext import commands

from config import CHANNEL_ROLES, CHANNEL_WELCOME
logger = logging.getLogger(__name__)


class WelcomeCog(commands.Cog):
    """Gère les messages de bienvenue pour les nouveaux membres."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Envoie un message de bienvenue dans le salon dédié."""
        if getattr(member, "bot", False):
            return
        channel = member.guild.get_channel(CHANNEL_WELCOME) if member.guild else None
        if channel:
            try:
                embed = discord.Embed(
                    title="🎉 Bienvenue au Refuge !",
                    description=(
                        f"{member.mention}, installe-toi bien !\n"
                        f"🕹️ Choisis ton rôle dans le salon <#{CHANNEL_ROLES}> pour accéder à toutes les sections.\n"
                        "Ravi de t’avoir parmi nous ! 🎮"
                    ),
                )
                embed.set_image(url=member.display_avatar.url)
                await channel.send(embed=embed)
            except discord.Forbidden:
                logger.warning(
                    "[welcome] Permissions insuffisantes pour envoyer le message de bienvenue",
                )
            except discord.NotFound:
                logger.warning(
                    "[welcome] Salon de bienvenue introuvable pour l'envoi du message",
                )
            except discord.HTTPException as e:
                logger.error(
                    "[welcome] Erreur HTTP lors de l'envoi du message de bienvenue: %s",
                    e,
                )
            except Exception:
                logger.exception(
                    "[welcome] Échec d'envoi du message de bienvenue pour %s", member.id,
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
