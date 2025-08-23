import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import ROULETTE_XP_CHANNEL_ID, DATA_DIR
from storage.roulette_xp_store import RouletteXPStore
from .xp import award_xp


@dataclass
class Bet:
    color: str
    amount: int


@dataclass
class RouletteRound:
    players: dict[int, Bet] = field(default_factory=dict)
    message: discord.Message | None = None
    task: asyncio.Task | None = None


class ObserveView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Observer", emoji="👀", style=discord.ButtonStyle.secondary)
    async def observe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_message("Tu observes la partie.", ephemeral=True)


class RouletteXPCog(commands.Cog):
    """Roulette XP collective avec classement mensuel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = RouletteXPStore(DATA_DIR, "roulette_xp")
        self.round: RouletteRound | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._ensure_scoreboard()

    async def _ensure_scoreboard(self) -> None:
        info = self.store.get_score_message()
        channel = self.bot.get_channel(ROULETTE_XP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return
        message = None
        if info:
            try:
                message = await channel.fetch_message(int(info["message_id"]))
            except Exception:
                message = None
        if message is None:
            message = await channel.send("🎰 Classement en préparation…")
            try:
                await message.pin()
            except discord.HTTPException:
                pass
            self.store.set_score_message(str(channel.id), str(message.id))
        await self._update_scoreboard()

    async def _update_scoreboard(self) -> None:
        channel = self.bot.get_channel(ROULETTE_XP_CHANNEL_ID)
        msg_info = self.store.get_score_message()
        if not isinstance(channel, discord.TextChannel) or not msg_info:
            return
        try:
            message = await channel.fetch_message(int(msg_info["message_id"]))
        except Exception:
            return
        stats = self.store.get_month()
        month = datetime.now().strftime("%B %Y")
        players = stats["players"]
        winners = sorted(
            ((uid, p["net"]) for uid, p in players.items() if p["net"] > 0),
            key=lambda x: x[1],
            reverse=True,
        )[:3]
        losers = sorted(
            ((uid, p["net"]) for uid, p in players.items() if p["net"] < 0),
            key=lambda x: x[1],
        )[:3]

        def fmt(uid: str, amt: int) -> str:
            member = channel.guild.get_member(int(uid)) if channel.guild else None
            name = member.display_name if member else f"<@{uid}>"
            return f"{name} — {amt:+d} XP"

        lines = [f"🎰 Classement du mois – {month}", "", "💎 TOP GAGNANTS"]
        if winners:
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid, amt) in enumerate(winners):
                prefix = medals[i] if i < len(medals) else f"{i+1}."
                lines.append(f"{prefix} {fmt(uid, amt)}")
        else:
            lines.append("Aucun gagnant pour le moment.")
        lines.extend(["", "💸 TOP PERDANTS"])
        if losers:
            for i, (uid, amt) in enumerate(losers, start=1):
                lines.append(f"{i}. {fmt(uid, amt)}")
        else:
            lines.append("Aucun perdant pour le moment.")

        lines.extend([
            "",
            "📊 Statistiques globales",
            f"XP parié ce mois : {stats['total_bet']} XP",
            f"XP gagné par la maison : {stats['house_gain']} XP",
        ])
        if stats["max_gain"]["amount"] > 0:
            uid = stats["max_gain"]["user_id"]
            member = channel.guild.get_member(int(uid)) if uid and channel.guild else None
            name = member.display_name if member else f"<@{uid}>"
            lines.append(
                f"Plus gros gain en une mise : {name} +{stats['max_gain']['amount']} XP"
            )
        if stats["max_loss"]["amount"] > 0:
            uid = stats["max_loss"]["user_id"]
            member = channel.guild.get_member(int(uid)) if uid and channel.guild else None
            name = member.display_name if member else f"<@{uid}>"
            lines.append(
                f"Plus grosse perte en une mise : {name} -{stats['max_loss']['amount']} XP"
            )
        await message.edit(content="\n".join(lines))

    async def _start_round(self) -> None:
        channel = self.bot.get_channel(ROULETTE_XP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return
        view = ObserveView()
        msg = await channel.send(
            "🎲 Nouvelle manche ! Utilise /roulettexp pour miser. 👀 pour observer.",
            view=view,
        )
        self.round = RouletteRound(message=msg)
        self.round.task = self.bot.loop.create_task(self._finish_round())

    async def _finish_round(self) -> None:
        await asyncio.sleep(10)
        if not self.round:
            return
        channel = self.bot.get_channel(ROULETTE_XP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            self.round = None
            return
        await channel.send("La bille tourne… 🎲 rien ne va plus…")
        await asyncio.sleep(3)
        result = random.choice(["red", "black"])
        emoji = "🟥" if result == "red" else "⬛"
        winners: list[tuple[int, int]] = []
        losers: list[tuple[int, int]] = []
        for uid, bet in self.round.players.items():
            delta = bet.amount if bet.color == result else -bet.amount
            if delta >= 0:
                winners.append((uid, delta))
            else:
                losers.append((uid, delta))
            await award_xp(uid, delta)
            self.store.record_result(str(uid), bet.amount, delta)
            member = channel.guild.get_member(uid)
            if delta >= 5000 and member:
                await channel.send(
                    random.choice(
                        [
                            f"💥 {member.mention} remporte {delta} XP !",
                            f"🎉 Incroyable ! {member.mention} gagne {delta} XP !",
                        ]
                    )
                )
            if delta <= -5000 and member:
                await channel.send(
                    random.choice(
                        [
                            f"😢 {member.mention} perd {abs(delta)} XP… merci pour la maison !",
                            f"🤡 Quelle générosité de {member.mention} : {abs(delta)} XP offerts !",
                        ]
                    )
                )
        lines = [f"Résultat : {emoji} {'Rouge' if result == 'red' else 'Noir'}"]
        if winners:
            lines.append("🏅 Gagnants")
            for uid, delta in winners:
                m = channel.guild.get_member(uid)
                name = m.display_name if m else str(uid)
                lines.append(f"• {name} +{delta} XP")
        if losers:
            lines.append("😞 Perdants")
            for uid, delta in losers:
                m = channel.guild.get_member(uid)
                name = m.display_name if m else str(uid)
                lines.append(f"• {name} {delta} XP")
        await channel.send("\n".join(lines))
        await self._update_scoreboard()
        self.round = None

    @app_commands.command(name="roulettexp", description="Parier de l'XP sur rouge ou noir")
    @app_commands.describe(couleur="Couleur choisie", mise="Montant d'XP misé")
    @app_commands.choices(
        couleur=[
            app_commands.Choice(name="Rouge", value="red"),
            app_commands.Choice(name="Noir", value="black"),
        ]
    )
    async def roulettexp(
        self,
        interaction: discord.Interaction,
        couleur: app_commands.Choice[str],
        mise: app_commands.Range[int, 1, 100000],
    ) -> None:
        if interaction.channel_id != ROULETTE_XP_CHANNEL_ID:
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans le salon de la roulette.",
                ephemeral=True,
            )
            return
        if self.round is None:
            await self._start_round()
        assert self.round is not None
        if interaction.user.id in self.round.players:
            await interaction.response.send_message(
                "Tu as déjà misé pour cette manche.", ephemeral=True
            )
            return
        self.round.players[interaction.user.id] = Bet(couleur.value, mise)
        self.store.record_bet(mise)
        await interaction.response.send_message(
            f"Mise acceptée : **{mise} XP** sur **{'Rouge' if couleur.value == 'red' else 'Noir'}**",
            ephemeral=True,
        )
        await self._update_scoreboard()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RouletteXPCog(bot))
