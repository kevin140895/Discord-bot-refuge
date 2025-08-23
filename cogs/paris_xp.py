import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import PARIS_XP_CHANNEL_ID, DATA_DIR
from storage.roulette_xp_store import RouletteXPStore
from .xp import award_xp


@dataclass
class Bet:
    color: str
    amount: int


@dataclass
class ParisXPRound:
    players: dict[int, Bet] = field(default_factory=dict)
    message: discord.Message | None = None
    task: asyncio.Task | None = None


class ObserveView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Observer", emoji="👀", style=discord.ButtonStyle.secondary)
    async def observe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_message("Tu observes la partie.", ephemeral=True)


class ParisXPCog(commands.Cog):
    """Mini-jeu ParisXP collectif avec classement mensuel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = RouletteXPStore(DATA_DIR, "paris_xp")
        self.round: ParisXPRound | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._ensure_scoreboard()

    async def _ensure_scoreboard(self) -> None:
        info = self.store.get_score_message()
        channel = self.bot.get_channel(PARIS_XP_CHANNEL_ID)
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
        channel = self.bot.get_channel(PARIS_XP_CHANNEL_ID)
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
        channel = self.bot.get_channel(PARIS_XP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return
        view = ObserveView()
        msg = await channel.send(
            "🎰 **Les jeux sont faits…** Placez vos paris avec /parisxp ! 👀 pour observer.",
            view=view,
        )
        self.round = ParisXPRound(message=msg)
        self.round.task = self.bot.loop.create_task(self._finish_round())

    async def _finish_round(self) -> None:
        await asyncio.sleep(10)
        if not self.round:
            return
        channel = self.bot.get_channel(PARIS_XP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            self.round = None
            return
        await channel.send("La bille roule… 🎲 rien ne va plus…")
        await asyncio.sleep(3)
        color = random.choice(["red", "black"])
        number = random.randrange(1 if color == "red" else 2, 37, 2)
        emoji = "🟥" if color == "red" else "⬛"
        lines = [f"Résultat : {emoji} {number} ({'Rouge' if color == 'red' else 'Noir'})"]
        players = list(self.round.players.items())
        for uid, bet in players:
            delta = bet.amount if bet.color == color else -bet.amount
            await award_xp(uid, delta)
            self.store.record_result(str(uid), bet.amount, delta)
            member = channel.guild.get_member(uid)
            name = member.display_name if member else str(uid)
            choice_emoji = "🟥" if bet.color == "red" else "⬛"
            outcome = "gagne" if delta > 0 else "perd"
            lines.append(
                f"• {name} – mise {bet.amount} XP sur {choice_emoji} "
                f"{'Rouge' if bet.color == 'red' else 'Noir'} – {outcome} {abs(delta)} XP"
            )
            if delta >= 5000 and member:
                await channel.send(
                    random.choice(
                        [
                            f"💥 {member.mention} fait sauter la banque et empoche {delta} XP !",
                            f"🎉 Chance insolente ! {member.mention} gagne {delta} XP !",
                        ]
                    )
                )
            if delta <= -5000 and member:
                await channel.send(
                    random.choice(
                        [
                            f"😢 {member.mention} perd {abs(delta)} XP… la maison te remercie !",
                            f"🤡 Aïe ! {member.mention} offre {abs(delta)} XP à la banque !",
                        ]
                    )
                )
        await channel.send("\n".join(lines))
        await self._maybe_surprise(channel)
        await self._update_scoreboard()
        self.round = None

    async def _maybe_surprise(self, channel: discord.TextChannel) -> None:
        if not self.round or not self.round.players:
            return
        players = list(self.round.players.items())
        if random.random() < 0.05:
            uid, bet = random.choice(players)
            await award_xp(uid, bet.amount)
            self.store.record_result(str(uid), 0, bet.amount)
            member = channel.guild.get_member(uid)
            mention = member.mention if member else f"<@{uid}>"
            await channel.send(
                f"🎟️ Coup de chance ! {mention} récupère sa mise ({bet.amount} XP) pour rejouer gratuitement !"
            )
            return
        if random.random() < 0.02:
            uid, _ = random.choice(players)
            bonus = 1000
            await award_xp(uid, bonus)
            self.store.record_result(str(uid), 0, bonus)
            member = channel.guild.get_member(uid)
            mention = member.mention if member else f"<@{uid}>"
            await channel.send(
                f"💎 Jackpot exceptionnel ! {mention} gagne {bonus} XP bonus !"
            )
            return
        if random.random() < 0.03 and len(players) >= 2:
            uid1, _ = random.choice(players)
            uid2, _ = random.choice([p for p in players if p[0] != uid1])
            for uid in (uid1, uid2):
                await award_xp(uid, 50)
                self.store.record_result(str(uid), 0, 50)
            m1 = channel.guild.get_member(uid1)
            m2 = channel.guild.get_member(uid2)
            mention1 = m1.mention if m1 else f"<@{uid1}>"
            mention2 = m2.mention if m2 else f"<@{uid2}>"
            await channel.send(
                f"🤝 XP partagé ! {mention1} et {mention2} gagnent chacun 50 XP !"
            )

    @app_commands.command(name="parisxp", description="Parier de l'XP sur rouge ou noir")
    @app_commands.describe(couleur="Couleur choisie", mise="Montant d'XP misé")
    @app_commands.choices(
        couleur=[
            app_commands.Choice(name="Rouge", value="red"),
            app_commands.Choice(name="Noir", value="black"),
        ]
    )
    async def parisxp(
        self,
        interaction: discord.Interaction,
        couleur: app_commands.Choice[str],
        mise: app_commands.Range[int, 1, 100000],
    ) -> None:
        if interaction.channel_id != PARIS_XP_CHANNEL_ID:
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
    await bot.add_cog(ParisXPCog(bot))
