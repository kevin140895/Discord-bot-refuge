from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    ANNOUNCE_CHANNEL_ID,
    PARI_XP_CHANNEL_ID,
    PARI_XP_ROLE_ID,
    CASINO_OPEN_HOUR,
    CASINO_CLOSE_HOUR,
    CASINO_SCHEDULE_LABEL,
)
from storage.xp_store import xp_store
from cogs.xp import award_xp
from utils import xp_adapter
from utils.timezones import PARIS_TZ
from utils.persistence import atomic_write_json_async, read_json_safe
from utils.interactions import safe_respond
from utils.metrics import measure
from utils.discord_utils import safe_message_edit

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(DATA_DIR, "pari_xp_state.json")
PARI_XP_MIN_BET = int(os.getenv("PARI_XP_MIN_BET", "10"))
PARI_XP_MAX_BET = int(os.getenv("PARI_XP_MAX_BET", "500"))
SPINNING_GIF_URL = (
    "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExcGpxaXd6ZDZhaGlvbXhjOTJtdDA5MTl5cGo2N2oxbHB2aXZpNjJtZiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/26uflBhaGt5lQsaCA/giphy.gif"
)
CASINO_CLOSED_MESSAGE = "🌙 Le Casino est fermé. Horaires : 10h00 - 02h00."


def _draw_number_for_roll(selected_number: int, roll: float) -> int:
    """Map one casino roll to a visible number while preserving custom odds.

    The casino reserves 3% for the house zero and 5% for the selected number.
    The remaining 92% is distributed across the other 35 numbers.
    """
    if not 1 <= selected_number <= 36:
        raise ValueError("selected_number must be between 1 and 36")
    if not 0 <= roll < 1:
        raise ValueError("roll must be in [0, 1)")
    if roll < 0.03:
        return 0
    if roll < 0.08:
        return selected_number

    other_numbers = [number for number in range(1, 37) if number != selected_number]
    position = (roll - 0.08) / 0.92
    index = min(int(position * len(other_numbers)), len(other_numbers) - 1)
    return other_numbers[index]


class BetAmountModal(discord.ui.Modal):
    def __init__(self, cog: "PariXPCog", bet_type: str) -> None:
        super().__init__(title="Parier XP")
        self.cog = cog
        self.bet_type = bet_type
        self.amount = discord.ui.TextInput(
            label="Mise (XP)", placeholder=f"{PARI_XP_MIN_BET}-{PARI_XP_MAX_BET}", min_length=1, max_length=4
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            amt = int(self.amount.value)
        except ValueError:
            await safe_respond(interaction, "❌ Montant invalide.", ephemeral=True)
            return
        await self.cog._handle_bet(interaction, self.bet_type, amt)


class NumberBetModal(discord.ui.Modal):
    def __init__(self, cog: "PariXPCog") -> None:
        super().__init__(title="Pari sur numéro")
        self.cog = cog
        self.amount = discord.ui.TextInput(
            label="Mise (XP)", placeholder=f"{PARI_XP_MIN_BET}-{PARI_XP_MAX_BET}", min_length=1, max_length=4
        )
        self.number = discord.ui.TextInput(
            label="Numéro (1-36)", placeholder="1-36", min_length=1, max_length=2
        )
        self.add_item(self.amount)
        self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            amt = int(self.amount.value)
            num = int(self.number.value)
            if not 1 <= num <= 36:
                raise ValueError
        except ValueError:
            await safe_respond(interaction, "❌ Valeurs invalides.", ephemeral=True)
            return
        await self.cog._handle_bet(interaction, "number", amt, num)


class RouletteXPView(discord.ui.View):
    def __init__(self, cog: "PariXPCog", disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if disabled:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="🔴 Rouge", style=discord.ButtonStyle.danger, custom_id="pari_xp:red")
    async def bet_red(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.cog.is_open:
            await interaction.response.send_modal(BetAmountModal(self.cog, "red"))
        else:
            await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)

    @discord.ui.button(label="⚫ Noir", style=discord.ButtonStyle.secondary, custom_id="pari_xp:black")
    async def bet_black(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.cog.is_open:
            await interaction.response.send_modal(BetAmountModal(self.cog, "black"))
        else:
            await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)

    @discord.ui.button(label="Pair", style=discord.ButtonStyle.primary, custom_id="pari_xp:even")
    async def bet_even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.cog.is_open:
            await interaction.response.send_modal(BetAmountModal(self.cog, "even"))
        else:
            await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)

    @discord.ui.button(label="Impair", style=discord.ButtonStyle.primary, custom_id="pari_xp:odd")
    async def bet_odd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.cog.is_open:
            await interaction.response.send_modal(BetAmountModal(self.cog, "odd"))
        else:
            await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)

    @discord.ui.button(label="Numéro", style=discord.ButtonStyle.success, custom_id="pari_xp:number")
    async def bet_number(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.cog.is_open:
            await interaction.response.send_modal(NumberBetModal(self.cog))
        else:
            await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)


class PariXPCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tz = PARIS_TZ
        self.state = read_json_safe(STATE_FILE)
        self.state.setdefault("is_open", False)
        self.state.setdefault("total_bets", 0)
        self.state.setdefault("total_winnings", 0)
        self.state.setdefault("players", {})
        self.is_open: bool = bool(self.state.get("is_open"))
        self._message_id: Optional[int] = self.state.get("message_id")
        self._last_announced_state: Optional[bool] = None
        self.check_schedule.start()

    # ── Schedule handling ──
    def _is_open_now(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or datetime.now(self.tz)
        h = dt.hour
        if CASINO_OPEN_HOUR < CASINO_CLOSE_HOUR:
            return CASINO_OPEN_HOUR <= h < CASINO_CLOSE_HOUR
        return h >= CASINO_OPEN_HOUR or h < CASINO_CLOSE_HOUR

    @tasks.loop(minutes=1)
    async def check_schedule(self) -> None:
        open_now = self._is_open_now()
        if open_now != self.is_open:
            self.is_open = open_now
            self.state["is_open"] = self.is_open
            await self._save_state()
            await self._announce_state()
        await self._ensure_roulette_message()

    @check_schedule.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    async def _announce_state(self) -> None:
        if self._last_announced_state == self.is_open:
            return
        if ANNOUNCE_CHANNEL_ID <= 0:
            self._last_announced_state = self.is_open
            return
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ANNOUNCE_CHANNEL_ID)
            except discord.HTTPException:
                return
        if not isinstance(channel, discord.TextChannel):
            return
        msg = (
            f"🎰 La roulette XP est maintenant ouverte jusqu'à {CASINO_CLOSE_HOUR:02d}h00 !"
            if self.is_open
            else f"🔒 La roulette XP est fermée. Rendez-vous à {CASINO_OPEN_HOUR:02d}h00."
        )
        try:
            await channel.send(msg)
        except discord.HTTPException:
            pass
        self._last_announced_state = self.is_open

    async def _save_state(self) -> None:
        await atomic_write_json_async(STATE_FILE, self.state)

    # ── Message & embed ──
    def _build_embed(self) -> discord.Embed:
        next_hour = (
            f"{CASINO_CLOSE_HOUR:02d}:00"
            if self.is_open
            else f"{CASINO_OPEN_HOUR:02d}:00"
        )
        status = "🟢 Ouvert" if self.is_open else "🔴 Fermé"
        desc = [
            f"Mise min : {PARI_XP_MIN_BET} XP",
            f"Mise max : {PARI_XP_MAX_BET} XP",
            "",
            "Probabilités :",
            "• Rouge/Noir : 45% → x2",
            "• Pair/Impair : 45% → x2",
            "• Numéro (1-36) : 5% → x10",
            "• Zéro Vert : 3% → 0x",
            "",
            f"État : {status} — {'ferme' if self.is_open else 'ouvre'} à ⏰ {next_hour}",
            f"Horaires du casino : {CASINO_SCHEDULE_LABEL}",
            "",
            f"Total misés : {self.state.get('total_bets', 0)} XP",
            f"Total gagnés : {self.state.get('total_winnings', 0)} XP",
        ]
        embed = discord.Embed(title="🎰 Pari XP", description="\n".join(desc))
        last = self.state.get("last_winner")
        if last:
            embed.add_field(
                name="Dernier gagnant",
                value=f"<@{last.get('user_id')}> a gagné {last.get('amount')} XP",
                inline=False,
            )
        return embed

    async def _ensure_roulette_message(self) -> None:
        channel = self.bot.get_channel(PARI_XP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(PARI_XP_CHANNEL_ID)
            except discord.HTTPException:
                return
        if not isinstance(channel, discord.TextChannel):
            return
        embed = self._build_embed()
        view = RouletteXPView(self, disabled=not self.is_open)
        message: Optional[discord.Message] = None
        if self._message_id:
            try:
                message = await channel.fetch_message(self._message_id)
            except discord.NotFound:
                message = None
        if message:
            await safe_message_edit(message, embed=embed, view=view)
        else:
            try:
                sent = await channel.send(embed=embed, view=view)
            except discord.HTTPException:
                return
            self._message_id = sent.id
            self.state["message_id"] = sent.id
            await self._save_state()

    def _record_player_result(self, user_id: int, bet_amount: int, payout: int) -> None:
        players = self.state.setdefault("players", {})
        if not isinstance(players, dict):
            players = {}
            self.state["players"] = players

        user_key = str(user_id)
        stats = players.get(user_key)
        if not isinstance(stats, dict):
            stats = {}
            players[user_key] = stats

        stats["bets"] = int(stats.get("bets", 0)) + 1
        stats["wagered"] = int(stats.get("wagered", 0)) + bet_amount
        stats["winnings"] = int(stats.get("winnings", 0)) + payout

    # ── Betting logic ──
    async def _handle_bet(
        self,
        interaction: discord.Interaction,
        bet_type: str,
        amount: int,
        number: Optional[int] = None,
    ) -> None:
        with measure("pari_xp_bet"):
            if not self.is_open:
                await safe_respond(interaction, CASINO_CLOSED_MESSAGE, ephemeral=True)
                return
            if amount < PARI_XP_MIN_BET or amount > PARI_XP_MAX_BET:
                await safe_respond(
                    interaction,
                    f"❌ Mise entre {PARI_XP_MIN_BET} et {PARI_XP_MAX_BET} XP.",
                    ephemeral=True,
                )
                return
            if bet_type == "number" and (number is None or not 1 <= number <= 36):
                await safe_respond(
                    interaction,
                    "❌ Numéro invalide (1-36).",
                    ephemeral=True,
                )
                return
            data = await xp_store.get_user_data(interaction.user.id)
            balance = int(data.get("xp", 0))
            if balance < amount:
                await safe_respond(interaction, "❌ XP insuffisant.", ephemeral=True)
                return
            try:
                await xp_adapter.add_xp(
                    interaction.user.id,
                    amount=-amount,
                    guild_id=interaction.guild_id or 0,
                    source="pari_xp",
                )
            except xp_adapter.InsufficientXPError:
                # Une autre opération a pu consommer le solde après le
                # pré-contrôle. Le débit atomique refuse alors proprement le pari.
                await safe_respond(interaction, "❌ XP insuffisant.", ephemeral=True)
                return
            except Exception as e:  # pragma: no cover - defensive
                logger.exception("[PariXP] debit failed: %s", e)
                await safe_respond(interaction, "❌ Erreur interne.", ephemeral=True)
                return

            roll = random.random()
            drawn_number: Optional[int] = None
            if bet_type == "number":
                assert number is not None
                drawn_number = _draw_number_for_roll(number, roll)
                zero_hit = drawn_number == 0
                win = drawn_number == number
                multiplier = 0 if zero_hit else 10
            else:
                zero_hit = roll < 0.03
                if zero_hit:
                    win = False
                    multiplier = 0
                else:
                    win = roll < 0.03 + 0.45
                    multiplier = 2

            payout = amount * multiplier if win else 0
            if win:
                try:
                    await award_xp(
                        interaction.user.id,
                        payout,
                        guild_id=interaction.guild_id,
                        source="pari_xp",
                    )
                except Exception as e:  # pragma: no cover - defensive
                    logger.exception("[PariXP] credit failed: %s", e)
                    await safe_respond(interaction, "❌ Erreur interne.", ephemeral=True)
                    return
                msg = f"🎉 Gagné ! Tu remportes {payout} XP."
                self.state["total_winnings"] = self.state.get("total_winnings", 0) + payout
                self.state["last_winner"] = {
                    "user_id": interaction.user.id,
                    "amount": payout,
                    "timestamp": datetime.now(self.tz).isoformat(),
                }
                if PARI_XP_ROLE_ID and interaction.guild:
                    role = interaction.guild.get_role(PARI_XP_ROLE_ID)
                    me = interaction.guild.me
                    if role and me and role < me.top_role:
                        try:
                            await interaction.user.add_roles(role, reason="Pari XP gagnant")
                        except discord.HTTPException:
                            pass
            else:
                msg = "❌ Perdu."
            if zero_hit:
                outcome_line = "🟢 Zéro Vert (0) ! La maison gagne."
            elif bet_type == "number":
                outcome_line = f"🎯 Numéro tiré : {drawn_number} — ton choix : {number}."
            else:
                outcome_line = "🎯 Pas de zéro vert cette fois."
            self.state["total_bets"] = self.state.get("total_bets", 0) + amount
            self._record_player_result(interaction.user.id, amount, payout)
            await self._save_state()
            result_embed = discord.Embed(
                title="🎰 Résultat",
                description=f"{outcome_line}\n{msg}",
            )
            spin_embed = discord.Embed(title="La roue tourne...")
            spin_embed.set_image(url=SPINNING_GIF_URL)
            await interaction.response.send_message(embed=spin_embed, ephemeral=True)
            message = await interaction.original_response()
            await asyncio.sleep(2.5)
            await message.edit(embed=result_embed)

    @app_commands.command(
        name="top_casino",
        description="Afficher le top 10 des performances au casino",
    )
    async def top_casino(self, interaction: discord.Interaction) -> None:
        players = self.state.get("players", {})
        if not isinstance(players, dict):
            players = {}

        leaderboard = []
        for user_id, payload in players.items():
            if not isinstance(payload, dict):
                continue
            try:
                bets = int(payload.get("bets", 0))
                wagered = int(payload.get("wagered", 0))
                winnings = int(payload.get("winnings", 0))
            except (TypeError, ValueError):
                continue
            if bets <= 0:
                continue
            net = winnings - wagered
            leaderboard.append((str(user_id), net, winnings, wagered, bets))

        if not leaderboard:
            embed = discord.Embed(
                title="🏆 Top Casino",
                description="Aucune activité casino enregistrée pour le moment.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        leaderboard.sort(
            key=lambda item: (item[1], item[2], -item[3]),
            reverse=True,
        )
        lines = []
        for idx, (user_id, net, winnings, wagered, bets) in enumerate(
            leaderboard[:10],
            start=1,
        ):
            member = None
            numeric_user_id: Optional[int] = None
            try:
                numeric_user_id = int(user_id)
            except (TypeError, ValueError):
                pass

            if interaction.guild and numeric_user_id is not None:
                member = interaction.guild.get_member(numeric_user_id)
            if member:
                display_name = member.display_name
            elif numeric_user_id is not None:
                try:
                    user = await self.bot.fetch_user(numeric_user_id)
                    display_name = user.display_name
                except discord.HTTPException:
                    display_name = f"Utilisateur {user_id}"
            else:
                display_name = f"Utilisateur {user_id}"

            bet_label = "pari" if bets == 1 else "paris"
            lines.append(
                f"**#{idx}** {display_name} — **{net:+d} XP net** · "
                f"{bets} {bet_label} · {wagered} misés · {winnings} gagnés"
            )

        embed = discord.Embed(
            title="🏆 Top Casino",
            description=(
                "Classement par résultat net (gains - mises).\n\n"
                + "\n".join(lines)
            ),
        )
        await interaction.response.send_message(embed=embed)

    async def cog_load(self) -> None:
        try:
            self.bot.add_view(RouletteXPView(self))
        except Exception:
            pass
        await self._ensure_roulette_message()

    def cog_unload(self) -> None:
        self.check_schedule.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PariXPCog(bot))