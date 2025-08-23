"""Roulette Refuge cog providing an isolated XP betting game."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List

import discord
from discord.ext import commands, tasks

from utils.timezones import now_paris, PARIS
from utils.storage import load_json, save_json
from utils.xp_adapter import get_balance, add_xp

DATA_DIR = Path("data/pari_xp")
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
TX_PATH = DATA_DIR / "transactions.json"
LEADERBOARD_PATH = DATA_DIR / "leaderboard.json"
class BetModal(discord.ui.Modal):
    def __init__(self, cog: "RouletteRefugeCog") -> None:
        super().__init__(title="🤑 Roulette Refuge", timeout=None, custom_id="pari_xp_modal")
        self.cog = cog
        self.amount = discord.ui.TextInput(label="Mise (XP)", custom_id="pari_xp_amount")
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            amount = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Mise invalide.", ephemeral=True)
            return
        await self.cog.process_bet(interaction, amount)


class HubView(discord.ui.View):
    def __init__(self, cog: "RouletteRefugeCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Miser XP", style=discord.ButtonStyle.green, custom_id="pari_xp_bet")
    async def bet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_modal(BetModal(self.cog))

    @discord.ui.button(label="📊 Leaderboard", style=discord.ButtonStyle.blurple, custom_id="pari_xp_leaderboard")
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.send_leaderboard(interaction)


class RouletteRefugeCog(commands.Cog):
    """Isolated roulette feature for XP betting."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Dict[str, Any] = load_json(CONFIG_PATH, {})
        self.state: Dict[str, Any] = load_json(STATE_PATH, {"hub_message_id": None, "leaderboard_message_id": None})
        self.leaderboard: Dict[str, int] = load_json(LEADERBOARD_PATH, {})
        self.transactions: List[Dict[str, Any]] = load_json(TX_PATH, [])
        self.last_bet: Dict[int, datetime] = {}
        self.daily_counts: Dict[int, int] = defaultdict(int)
        self.current_day = now_paris().date()
        self.open = False
        self.scheduler_task.start()
        self.leaderboard_task.start()

    # -------------------------- Utility methods --------------------------
    def is_open(self, now: datetime | None = None) -> bool:
        now = now or now_paris()
        t = now.time()
        return t >= time(8, 0) or t < time(2, 0)

    async def ensure_messages(self) -> None:
        channel_id = int(self.config.get("hub_channel_id", 0))
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        view = HubView(self)
        # Hub message
        msg_id = self.state.get("hub_message_id")
        message: discord.Message | None = None
        if msg_id:
            try:
                message = await channel.fetch_message(int(msg_id))
            except Exception:
                message = None
        if message is None:
            embed = self._hub_embed()
            message = await channel.send(embed=embed, view=view)
            self.state["hub_message_id"] = message.id
            await save_json(STATE_PATH, self.state)
        else:
            await message.edit(embed=self._hub_embed(), view=view)
        # Leaderboard message
        lb_id = self.state.get("leaderboard_message_id")
        lb_msg: discord.Message | None = None
        if lb_id:
            try:
                lb_msg = await channel.fetch_message(int(lb_id))
            except Exception:
                lb_msg = None
        if lb_msg is None:
            lb_msg = await channel.send(embed=self._leaderboard_embed())
            try:
                await lb_msg.pin()
            except Exception:
                pass
            self.state["leaderboard_message_id"] = lb_msg.id
            await save_json(STATE_PATH, self.state)
        self.hub_message = message
        self.leaderboard_message = lb_msg

    def _hub_embed(self) -> discord.Embed:
        desc = "🟢 Ouvert — ferme à 02:00" if self.is_open() else "🔴 Fermé — ouvre à 08:00"
        return discord.Embed(title="🤑 Roulette Refuge", description=desc, color=discord.Color.green())

    def _leaderboard_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🏆 Leaderboard Roulette Refuge")
        if not self.leaderboard:
            embed.description = "Aucun pari pour le moment."
            return embed
        sorted_lb = sorted(self.leaderboard.items(), key=lambda x: x[1], reverse=True)[:10]
        lines = []
        for rank, (uid, gain) in enumerate(sorted_lb, start=1):
            lines.append(f"{rank}. <@{uid}> — {gain:+d} XP")
        embed.description = "\n".join(lines)
        return embed

    async def refresh_leaderboard(self) -> None:
        embed = self._leaderboard_embed()
        if getattr(self, "leaderboard_message", None):
            try:
                await self.leaderboard_message.edit(embed=embed)
            except Exception:
                pass

    async def send_leaderboard(self, interaction: discord.Interaction) -> None:
        if getattr(self, "leaderboard_message", None):
            await interaction.response.send_message(f"Leaderboard : {self.leaderboard_message.jump_url}", ephemeral=True)
        else:
            await interaction.response.send_message("Leaderboard indisponible.", ephemeral=True)

    async def process_bet(self, interaction: discord.Interaction, amount: int) -> None:
        now = now_paris()
        if not self.is_open(now):
            await interaction.response.send_message("La roulette est fermée.", ephemeral=True)
            return
        if now.time() >= time(1, 45):
            await interaction.response.send_message("Dernier appel passé !", ephemeral=True)
            return
        if amount < int(self.config.get("min_bet", 5)):
            await interaction.response.send_message("Mise trop faible.", ephemeral=True)
            return
        balance = get_balance(interaction.user.id)
        if balance < max(amount, int(self.config.get("min_balance", 10))):
            await interaction.response.send_message("Solde insuffisant.", ephemeral=True)
            return
        if (now - interaction.user.created_at.replace(tzinfo=PARIS)).days < 2:
            await interaction.response.send_message("Ancienneté insuffisante.", ephemeral=True)
            return
        last = self.last_bet.get(interaction.user.id)
        if last and (now - last).total_seconds() < int(self.config.get("cooldown_seconds", 15)):
            await interaction.response.send_message("Patiente un peu avant de rejouer.", ephemeral=True)
            return
        if self.daily_counts[interaction.user.id] >= int(self.config.get("daily_bet_cap", 20)):
            await interaction.response.send_message("Cap quotidien atteint.", ephemeral=True)
            return

        outcome = self._draw()
        net = self._compute_net(amount, outcome)
        await add_xp(interaction.user.id, net)
        self.last_bet[interaction.user.id] = now
        self.daily_counts[interaction.user.id] += 1

        segment = str(outcome)
        tx = {
            "user": interaction.user.id,
            "amount": amount,
            "net": net,
            "segment": segment,
            "ts": now.isoformat(),
        }
        if outcome == "double":
            tx["segment"] = "double_xp_1h"
            tx["notes"] = "placeholder only"
        elif outcome == "ticket":
            tx["segment"] = "ticket_free"
            tx["notes"] = "placeholder only"

        self.transactions.append(tx)
        await save_json(TX_PATH, self.transactions)
        self.leaderboard[str(interaction.user.id)] = self.leaderboard.get(str(interaction.user.id), 0) + net
        await save_json(LEADERBOARD_PATH, self.leaderboard)
        await self.refresh_leaderboard()

        msg = self._result_message(amount, net, outcome)
        await interaction.response.send_message(msg, ephemeral=True)

        channel = self.bot.get_channel(int(self.config.get("hub_channel_id", 0)))
        if channel and (net >= amount * 4 or net <= -100 or outcome == "jackpot"):
            if outcome == "jackpot":
                content = f"@here {interaction.user.mention} remporte le **Super Jackpot** (+1000 XP) !"
            elif net >= amount * 4:
                content = f"{interaction.user.mention} gagne **{net:+d} XP** à la roulette !"
            else:
                content = f"{interaction.user.mention} perd **{abs(net)} XP** à la roulette…"
            await channel.send(content)

    def _compute_net(self, amount: int, outcome: Any) -> int:
        if outcome == "jackpot":
            return 1000
        if isinstance(outcome, (int, float)):
            return int(amount * (outcome - 1))
        return -amount

    def _draw(self) -> Any:
        r = random.random()
        if r < 0.40:
            return 0
        if r < 0.58:
            return 0.5
        if r < 0.72:
            return 1
        if r < 0.86:
            return 2
        if r < 0.95:
            return 5
        if r < 0.98:
            return 10
        if r < 0.983:
            return "ticket"
        if r < 0.99:
            return "double"
        if r < 0.993:
            return "jackpot"
        return 0

    def _result_message(self, amount: int, net: int, outcome: Any) -> str:
        if outcome == "jackpot":
            return "🎉 Super Jackpot ! Tu gagnes 1000 XP !"
        if outcome == "double":
            return "⚡ Tu as gagné un boost Double XP (1h) ! (placeholder)"
        if outcome == "ticket":
            return "🎟️ Tu as gagné un ticket gratuit ! (placeholder)"
        if net > 0:
            return f"Tu gagnes {net:+d} XP !"
        if net == 0:
            return "Égalité, tu récupères ta mise."
        return f"Tu perds {net} XP."

    async def daily_summary(self) -> None:
        channel = self.bot.get_channel(int(self.config.get("hub_channel_id", 0)))
        if channel is None or not self.transactions:
            return
        totals: Dict[int, int] = defaultdict(int)
        biggest = (0, 0)
        total_bet = 0
        total_net = 0
        for tx in self.transactions:
            uid = tx["user"]
            net = tx["net"]
            amount = tx["amount"]
            totals[uid] += net
            total_bet += amount
            total_net += amount + net
            if net > biggest[1]:
                biggest = (uid, net)
        winners = sorted(((u, g) for u, g in totals.items() if g > 0), key=lambda x: x[1], reverse=True)[:3]
        losers = sorted(((u, g) for u, g in totals.items() if g < 0), key=lambda x: x[1])[:3]
        embed = discord.Embed(title="📊 Résumé quotidien Roulette Refuge")
        if winners:
            embed.add_field(name="Top gagnants", value="\n".join(f"<@{u}> {g:+d} XP" for u, g in winners), inline=False)
        if losers:
            embed.add_field(name="Top perdants", value="\n".join(f"<@{u}> {g:+d} XP" for u, g in losers), inline=False)
        if biggest[1] > 0:
            embed.add_field(name="Plus gros gain", value=f"<@{biggest[0]}> {biggest[1]} XP", inline=False)
        embed.add_field(name="Total misé", value=f"{total_bet} XP")
        embed.add_field(name="Total redistribué", value=f"{total_net} XP")
        await channel.send(embed=embed)

    # -------------------------- Tasks --------------------------
    @tasks.loop(minutes=1)
    async def scheduler_task(self) -> None:
        now = now_paris()
        day = now.date()
        if day != self.current_day:
            self.current_day = day
            self.daily_counts.clear()
            self.transactions.clear()
            await save_json(TX_PATH, self.transactions)
        is_open = self.is_open(now)
        if is_open != self.open:
            self.open = is_open
            await self.ensure_messages()
            if not self.open:
                await self.daily_summary()
        if self.hub_message:
            try:
                await self.hub_message.edit(embed=self._hub_embed())
            except Exception:
                pass

    @scheduler_task.before_loop
    async def before_scheduler(self) -> None:
        await self.bot.wait_until_ready()
        await self.ensure_messages()
        self.open = self.is_open()

    @tasks.loop(minutes=7)
    async def leaderboard_task(self) -> None:
        await self.refresh_leaderboard()

    @leaderboard_task.before_loop
    async def before_leaderboard(self) -> None:
        await self.bot.wait_until_ready()
        await self.ensure_messages()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RouletteRefugeCog(bot))
