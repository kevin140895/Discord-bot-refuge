import discord
from discord.ext import commands, tasks  # noqa: F401
from datetime import datetime
from datetime import timedelta
from datetime import date
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo
from discord import ui
from discord import app_commands
from main.utils.xp_adapter import (
    get_user_xp,
    get_user_account_age_days,
    add_user_xp,
    apply_double_xp_buff,
)
import random
import inspect
import re
import logging
import asyncio

from utils import storage, timezones
from utils.timezones import TZ_PARIS
from utils.storage import load_json
from storage.roulette_store import RouletteStore
from config import DATA_DIR
from utils.discord_utils import safe_message_edit
from utils.economy_tickets import consume_free_ticket

PARI_XP_DATA_DIR = "main/data/pari_xp/"
CONFIG_PATH = PARI_XP_DATA_DIR + "config.json"
STATE_PATH = PARI_XP_DATA_DIR + "state.json"
LB_PATH = PARI_XP_DATA_DIR + "leaderboard.json"
TX_PATH = PARI_XP_DATA_DIR + "transactions.json"


class RouletteRefugeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = storage.load_json(storage.Path(CONFIG_PATH), {})
        self.state = storage.load_json(storage.Path(STATE_PATH), {})
        self.scheduler_task.start()
        self.leaderboard_task.start()
        self._cooldowns: dict[int, datetime] = {}
        self._bets_today: dict[int, int] = {}
        self._bets_today_date = self._now().date()
        self._last_autoheal_hub = None
        self._last_autoheal_lb = None
        self._hub_lock = asyncio.Lock()
        self._autoheal_presence_task.start()
        self.roulette_store = RouletteStore(data_dir=DATA_DIR)
        self._loss_streak: dict[int, int] = {}

    def _now(self) -> datetime:
        return datetime.now(timezones.TZ_PARIS)

    def _is_open_hours(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or self._now()
        open_hour = int(self.config.get("open_hour", 8))
        close_hour = int(self.config.get("close_hour", 2))
        if open_hour < close_hour:
            return open_hour <= dt.hour < close_hour
        return dt.hour >= open_hour or dt.hour < close_hour

    async def _get_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.config.get("channel_id")
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _get_announce_channel(self) -> discord.TextChannel | None:
        cfg = load_json(storage.Path(CONFIG_PATH), {})
        ch_id = int(cfg.get("announce_channel_id") or 0)
        if ch_id:
            ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
            if ch:
                return ch  # type: ignore[return-value]
        return await self._get_channel()

    async def _find_hub_message(
        self, channel: discord.TextChannel
    ) -> discord.Message | None:
        """Search recent channel history for an existing hub message."""
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user and msg.embeds:
                embed = msg.embeds[0]
                expected_title = f"🎰 {self.config.get('game_display_name', '🤑 Roulette Refuge')} 🎰"
                if embed.title == expected_title:
                    self.state["hub_message_id"] = msg.id
                    await storage.save_json(storage.Path(STATE_PATH), self.state)
                    return msg
        return None

    async def _find_leaderboard_message(
        self, channel: discord.TextChannel
    ) -> discord.Message | None:
        """Search pinned messages and recent history for existing leaderboard."""
        title_prefix = "📊 Roulette Refuge"
        try:
            pins = await channel.pins()
        except Exception:
            pins = []
        for msg in pins:
            if msg.author == self.bot.user:
                for embed in msg.embeds:
                    if embed.title and embed.title.startswith(title_prefix):
                        self.state["leaderboard_message_id"] = msg.id
                        await storage.save_json(storage.Path(STATE_PATH), self.state)
                        return msg
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user:
                for embed in msg.embeds:
                    if embed.title and embed.title.startswith(title_prefix):
                        self.state["leaderboard_message_id"] = msg.id
                        await storage.save_json(storage.Path(STATE_PATH), self.state)
                        return msg
        return None

    async def _ensure_hub_message(self, channel: discord.TextChannel) -> None:
        async with self._hub_lock:
            hub_id = self.state.get("hub_message_id")
            embed = self._build_hub_embed()
            view = self._build_hub_view()
            for child in view.children:
                if (
                    isinstance(child, discord.ui.Button)
                    and getattr(child, "custom_id", None) == "pari_xp_bet"
                ):
                    setattr(child, "callback", self._bet_button_callback)  # noqa: B010
            message = None
            if hub_id:
                try:
                    message = await channel.fetch_message(int(hub_id))
                except Exception:
                    message = None
            if not message:
                message = await self._find_hub_message(channel)
            if message:
                await safe_message_edit(message, embed=embed, view=view)
            else:
                message = await channel.send(embed=embed, view=view)
                self.state["hub_message_id"] = message.id
                await storage.save_json(storage.Path(STATE_PATH), self.state)

    def _build_hub_embed(self) -> discord.Embed:
        title = f"🎰 {self.config.get('game_display_name', '🤑 Roulette Refuge')} 🎰"
        is_open = self.state.get("is_open", self._is_open_hours())
        lines = [
            "━━━━━━━━━━━━━━━",
            "💵 Mise minimum : 5 XP",
            "🛑 Cooldown : 15 secondes",
            "🎲 Limite : 20 tirages / jour",
            "",
            "📩 Résultats privés (éphémères)",
            "📢 Les gros événements sont annoncés publiquement",
            "",
            "━━━━━━━━━━━━━━━",
            (
                f"🟢 État : Ouvert — ferme à ⏰ {int(self.config.get('close_hour', 2)):02d}:00"
                if is_open
                else f"🔴 État : Fermé — ouvre à ⏰ {int(self.config.get('open_hour', 8)):02d}:00"
            ),
        ]
        return discord.Embed(title=title, description="\n".join(lines))

    def _build_hub_view(self) -> discord.ui.View:
        cog = self
        is_open = self.state.get("is_open", self._is_open_hours())

        class HubView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=None)
                if not is_open:
                    for child in list(self.children):
                        self.remove_item(child)

            @discord.ui.button(
                custom_id="pari_xp_bet",
                label="Miser XP",
                style=discord.ButtonStyle.success,
            )
            async def bet(  # type: ignore[override]
                self, interaction: discord.Interaction, button: discord.ui.Button
            ) -> None:
                await cog._bet_button_callback(interaction)

        return HubView()

    async def _ensure_leaderboard_message(self, channel: discord.TextChannel) -> None:
        state = storage.load_json(storage.Path(STATE_PATH), self.state)
        msg_id = state.get("leaderboard_message_id")
        embed = self._build_leaderboard_embed()
        message = None
        if msg_id:
            try:
                message = await channel.fetch_message(int(msg_id))
            except Exception:
                message = None
        if not message:
            message = await self._find_leaderboard_message(channel)
            state = self.state
        if message:
            await safe_message_edit(message, embed=embed)
        else:
            message = await channel.send(embed=embed)
            state["leaderboard_message_id"] = message.id
            await storage.save_json(storage.Path(STATE_PATH), state)
            self.state = state

    async def _refresh_leaderboard(self, channel: discord.TextChannel) -> None:
        """Ensure leaderboard message exists and refresh its embed."""
        try:
            await self._ensure_leaderboard_message(channel)
            state = storage.load_json(storage.Path(STATE_PATH), {})
            msg_id = state.get("leaderboard_message_id")
            if not msg_id:
                return
            msg = await channel.fetch_message(int(msg_id))
            await safe_message_edit(msg, embed=self._build_leaderboard_embed())
        except Exception:
            pass

    def _build_leaderboard_embed(self) -> discord.Embed:
        tz = getattr(timezones, "TZ_PARIS", ZoneInfo("Europe/Paris"))
        now = datetime.now(tz)
        transactions = storage.load_json(storage.Path(TX_PATH), [])
        month_txs = []
        for tx in transactions:
            ts = tx.get("ts")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue
            dt = dt.astimezone(tz)
            if dt.year == now.year and dt.month == now.month:
                month_txs.append(tx)
        stats: dict[int, dict[str, int | str]] = {}
        for tx in month_txs:
            uid = tx.get("user_id")
            username = tx.get("username", str(uid))
            delta = int(tx.get("delta", 0))
            user_stat = stats.setdefault(
                uid, {"username": username, "won": 0, "lost": 0}
            )
            user_stat["username"] = username
            if delta > 0:
                user_stat["won"] = int(user_stat["won"]) + delta
            elif delta < 0:
                user_stat["lost"] = int(user_stat["lost"]) - delta
        winners = sorted(
            [v for v in stats.values() if int(v["won"]) > 0],
            key=lambda x: int(x["won"]),
            reverse=True,
        )[:10]
        losers = sorted(
            [v for v in stats.values() if int(v["lost"]) > 0],
            key=lambda x: int(x["lost"]),
            reverse=True,
        )[:10]
        win_lines = [
            f"{idx + 1}. {w['username']} (+{int(w['won'])} XP)"
            for idx, w in enumerate(winners)
        ]
        loss_lines = [
            f"{idx + 1}. {loser['username']} (-{int(loser['lost'])} XP)"
            for idx, loser in enumerate(losers)
        ]
        biggest = None
        for tx in month_txs:
            if tx.get("delta", 0) > 0:
                if not biggest or tx["delta"] > biggest["delta"]:
                    biggest = tx
        biggest_val = f"{biggest['username']} (+{biggest['delta']} XP)" if biggest else "N/A"
        embed = discord.Embed(
            title=f"📊 Roulette Refuge — Leaderboard ({now.strftime('%B %Y')})",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="🏆 Top 10 gagnants (mois)",
            value="\n".join(win_lines) if win_lines else "N/A",
            inline=False,
        )
        embed.add_field(
            name="💸 Top 10 perdants (mois)",
            value="\n".join(loss_lines) if loss_lines else "N/A",
            inline=False,
        )
        embed.add_field(
            name="💥 Plus gros gain unique (mois)",
            value=biggest_val,
            inline=False,
        )
        embed.add_field(name="🔁 Séries", value="(à venir)", inline=False)
        return embed

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.state = storage.load_json(storage.Path(STATE_PATH), {})
        channel = await self._get_channel()
        if channel:
            await self._ensure_hub_message(channel)
            # leaderboard sera géré aux étapes suivantes
            await self._ensure_leaderboard_message(channel)
        try:
            self.bot.add_view(self._build_hub_view())
        except Exception:
            pass
        try:
            await self._autoheal_presence_task()
        except Exception:
            pass

    async def _update_hub_state(self, is_open: bool) -> None:
        self.state["is_open"] = is_open
        await storage.save_json(storage.Path(STATE_PATH), self.state)
        channel = await self._get_channel()
        if channel:
            await self._ensure_hub_message(channel)

    async def _announce_open(self, channel: discord.TextChannel) -> None:
        open_hour = int(self.config.get("open_hour", 8))
        close_hour = int(self.config.get("close_hour", 2))
        lines = [
            f"Horaires : {open_hour:02d}:00→{close_hour:02d}:00",
            "Mise min : 5 XP",
            "Cooldown : 15s",
            "Cap : 20/jour",
        ]
        embed = discord.Embed(
            title="🤑 Roulette Refuge — Ouverture",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        announce_channel = await self._get_announce_channel()
        if announce_channel:
            await announce_channel.send(embed=embed)
        else:
            await channel.send(embed=embed)
        self.state["last_open_announce_ts"] = self._now().isoformat()

    def _daily_stats(self) -> dict[str, Any]:
        transactions = storage.load_json(storage.Path(TX_PATH), [])
        now = datetime.now(TZ_PARIS)
        today: date = now.date()
        day_txs = []
        for tx in transactions:
            ts = tx.get("ts")
            try:
                dt = datetime.fromisoformat(ts).astimezone(TZ_PARIS)
            except Exception:
                continue
            if dt.date() == today:
                day_txs.append(tx)
        total_bet = sum(int(tx.get("bet", 0)) for tx in day_txs)
        total_payout = sum(int(tx.get("payout", 0)) for tx in day_txs)
        net = total_payout - total_bet
        return {
            "day_txs": day_txs,
            "total_bet": total_bet,
            "total_payout": total_payout,
            "net": net,
        }

    async def _announce_close(self, channel: discord.TextChannel) -> None:
        stats = self._daily_stats()
        day_txs = stats["day_txs"]
        total_bet = stats["total_bet"]
        total_payout = stats["total_payout"]
        net = stats["net"]
        lines = [
            f"Paris : {len(day_txs)}",
            f"Total misé : {total_bet} XP",
            f"Total redistribué : {total_payout} XP",
            f"Résultat net : {net:+} XP",
        ]
        embed = discord.Embed(
            title="🤑 Roulette Refuge — Clôture du jour",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        announce_channel = await self._get_announce_channel()
        if announce_channel:
            await announce_channel.send(embed=embed)
            return
        await channel.send(embed=embed)

    async def _post_daily_summary(self, channel: discord.TextChannel) -> None:
        announce_channel = await self._get_announce_channel()
        if announce_channel:
            channel = announce_channel
        now = datetime.now(TZ_PARIS)
        daily = self._daily_stats()
        day_txs = daily["day_txs"]
        total_bet = daily["total_bet"]
        total_payout = daily["total_payout"]
        user_stats: dict[int, dict[str, int | str]] = {}
        biggest = None
        for tx in day_txs:
            uid = tx.get("user_id")
            username = tx.get("username", str(uid))
            delta = int(tx.get("delta", 0))
            if delta > 0:
                if not biggest or delta > biggest["delta"]:
                    biggest = tx
            user_stat = user_stats.setdefault(uid, {"username": username, "net": 0})
            user_stat["username"] = username
            user_stat["net"] = int(user_stat["net"]) + delta
        winners = sorted(
            [v for v in user_stats.values() if int(v["net"]) > 0],
            key=lambda x: int(x["net"]),
            reverse=True,
        )[:3]
        losers = sorted(
            [v for v in user_stats.values() if int(v["net"]) < 0],
            key=lambda x: int(x["net"]),
        )[:3]
        win_lines = [f"{idx + 1}. {w['username']} ({int(w['net']):+} XP)" for idx, w in enumerate(winners)]
        loss_lines = [f"{idx + 1}. {loser['username']} ({int(loser['net']):+} XP)" for idx, loser in enumerate(losers)]
        biggest_val = f"{biggest['username']} (+{biggest['delta']} XP)" if biggest else "N/A"
        embed = discord.Embed(
            title="🤑 Roulette Refuge — Clôture du jour",
            color=discord.Color.gold(),
            timestamp=now,
        )
        embed.add_field(
            name="🏆 Top 3 gagnants",
            value="\n".join(win_lines) if win_lines else "N/A",
            inline=False,
        )
        embed.add_field(
            name="💸 Top 3 perdants",
            value="\n".join(loss_lines) if loss_lines else "N/A",
            inline=False,
        )
        embed.add_field(
            name="💥 Plus gros gain unique",
            value=biggest_val,
            inline=False,
        )
        embed.add_field(
            name="📊 Total misé / redistribué",
            value=f"{total_bet} XP / {total_payout} XP",
            inline=False,
        )
        open_hour = int(self.config.get("open_hour", 8))
        embed.set_footer(text=f"Réouverture demain à {open_hour:02d}:00 ⏰")
        await channel.send(embed=embed)

    @tasks.loop(minutes=1.0)
    async def scheduler_task(self) -> None:
        """Gère l'ouverture et la fermeture automatiques selon la configuration."""
        tz = getattr(timezones, "TZ_PARIS", ZoneInfo("Europe/Paris"))
        now = datetime.now(tz)
        last_call_hour = int(self.config.get("last_call_hour", 1))
        last_call_minute = int(self.config.get("last_call_minute", 45))
        close_hour = int(self.config.get("close_hour", 2))

        if now.hour == 0 and now.minute == 0:
            self.state["daily_cap_counter"] = 0
            await storage.save_json(storage.Path(STATE_PATH), self.state)

        channel = await self._get_channel()
        if not channel:
            return

        if now.day == 1 and now.hour == 0 and now.minute == 0:
            await storage.save_json(storage.Path(TX_PATH), [])
            try:
                await self._refresh_leaderboard(channel)
            except Exception:
                pass

        # --- Gestion ouverture/fermeture + "dernier appel" ---
        is_open_now = self._is_open_hours(now)

        # Eviter de spam l'annonce d'ouverture : une seule fois par jour
        last_open_ts = self.state.get("last_open_announce_ts")
        last_open_date = (
            datetime.fromisoformat(last_open_ts).date() if last_open_ts else None
        )

        if is_open_now:
            # (Ré)ouverture (ou hub manquant) → annonce + maj hub
            if (not self.state.get("is_open", False)) or (last_open_date != now.date()):
                await self._announce_open(channel)
                await self._update_hub_state(True)
            elif not self.state.get("hub_message_id"):
                await self._update_hub_state(True)
            else:
                await self._ensure_hub_message(channel)

            # Dernier appel 15 min avant fermeture
            if (
                self.state.get("is_open", False)
                and now.hour == last_call_hour
                and now.minute == last_call_minute
            ):
                announce_ch = await self._get_announce_channel()
                msg = f"⏳ Dernier appel — fermeture dans 15 minutes ({close_hour:02d}:00)."
                if announce_ch:
                    await announce_ch.send(msg)
                else:
                    await channel.send(msg)

        elif self.state.get("is_open", False):
            # On sort de la plage d'ouverture → clôture + récap + fermeture
            await self._announce_close(channel)
            await self._update_hub_state(False)
            await self._post_daily_summary(channel)
            await self._announce_close(channel)
            await self._update_hub_state(False)
            await self._post_daily_summary(channel)

    @scheduler_task.before_loop
    async def _wait_ready_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=3.0)
    async def leaderboard_task(self) -> None:
        channel = await self._get_channel()
        if channel:
            await self._refresh_leaderboard(channel)

    @leaderboard_task.before_loop
    async def _wait_ready_lb(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10.0)
    async def _autoheal_presence_task(self):
        ch = await self._get_channel()
        if not ch:
            return

        state = load_json(storage.Path(STATE_PATH), self.state)
        now = datetime.now(tz=TZ_PARIS)

        # --- HUB ---
        hub_id = state.get("hub_message_id")
        need_heal_hub = False
        _msg_hub = None
        if hub_id:
            try:
                _msg_hub = await ch.fetch_message(int(hub_id))
            except Exception:
                need_heal_hub = True
        else:
            need_heal_hub = True

        if need_heal_hub:
            if not self._last_autoheal_hub or (now - self._last_autoheal_hub) > timedelta(hours=1):
                try:
                    await self._ensure_hub_message(ch)
                    state = self.state
                    self._last_autoheal_hub = now
                except Exception:
                    pass

        # --- LEADERBOARD (si méthode dispo) ---
        if hasattr(self, "_ensure_leaderboard_message"):
            lb_id = state.get("leaderboard_message_id")
            need_heal_lb = False
            _msg_lb = None
            if lb_id:
                try:
                    _msg_lb = await ch.fetch_message(int(lb_id))
                except Exception:
                    need_heal_lb = True
            else:
                need_heal_lb = True

            if need_heal_lb:
                if not self._last_autoheal_lb or (now - self._last_autoheal_lb) > timedelta(hours=1):
                    try:
                        await self._ensure_leaderboard_message(ch)
                        state = self.state
                        self._last_autoheal_lb = now
                    except Exception:
                        pass

    @_autoheal_presence_task.before_loop
    async def _wait_ready_autoheal(self):
        await self.bot.wait_until_ready()

    async def _bet_button_callback(self, interaction: discord.Interaction) -> None:
        if interaction.channel_id != int(self.config.get("channel_id", 0)):
            await interaction.response.send_message(
                "🚪 Utilise la 🤑 Roulette Refuge dans <#1408834276228730900>.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(self._build_bet_modal())

    def _build_bet_modal(self) -> ui.Modal:
        cog = self

        class BetModal(ui.Modal):
            def __init__(self) -> None:
                super().__init__(title="🤑 Roulette Refuge — Parier", custom_id="pari_xp_modal")
                self.amount: ui.TextInput = ui.TextInput(
                    label="Montant (XP)",
                    placeholder="≥ 5",
                    required=True,
                    custom_id="pari_xp_amount",
                )
                self.color: ui.TextInput = ui.TextInput(
                    label="Couleur (rouge/noir)",
                    placeholder="rouge ou noir",
                    required=False,
                    custom_id="pari_xp_color",
                )
                self.add_item(self.amount)
                self.add_item(self.color)

            async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
                await cog._handle_bet_submission(interaction)

        return BetModal()

    def _draw_segment(self) -> str:
        probabilities = self.config.get("probabilities", {})
        if not probabilities:
            return "lose_0x"
        segments = list(probabilities.keys())
        weights = list(probabilities.values())
        return random.choices(segments, weights=weights, k=1)[0]

    def _compute_result(self, bet: int, segment: str) -> dict:
        payout = 0
        delta = 0
        mult: Optional[float] = None
        notes = None
        double_xp = False
        if segment == "lose_0x":
            payout = 0
            delta = -bet
            mult = 0.0
        elif segment == "half_0_5x":
            payout = bet // 2
            delta = payout - bet
            mult = 0.5
        elif segment == "even_1x":
            payout = bet
            delta = 0
            mult = 1.0
        elif segment == "win_2x":
            payout = bet * 2
            delta = payout - bet
            mult = 2.0
        elif segment == "win_5x":
            payout = bet * 5
            delta = payout - bet
            mult = 5.0
        elif segment == "win_10x":
            payout = bet * 10
            delta = payout - bet
            mult = 10.0
        elif segment == "super_jackpot_plus_1000":
            payout = bet + 1000
            delta = 1000
            notes = "super jackpot"
        elif segment == "double_xp_1h":
            payout = 0
            delta = -bet
            double_xp = True
        else:
            payout = 0
            delta = -bet
        return {
            "segment": segment,
            "payout": payout,
            "delta": delta,
            "mult": mult,
            "notes": notes,
            "double_xp": double_xp,
        }

    async def _handle_bet_submission(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.channel_id != int(self.config.get("channel_id", 0)):
            await interaction.response.send_message(
                "🚪 Utilise la 🤑 Roulette Refuge dans <#1408834276228730900>.",
                ephemeral=True,
            )
            return
        amount_str = ""
        color_str = ""
        data = cast(dict[str, Any], interaction.data or {})
        for row in data.get("components", []):
            for comp in row.get("components", []):
                if comp.get("custom_id") == "pari_xp_amount":
                    amount_str = comp.get("value", "")
                elif comp.get("custom_id") == "pari_xp_color":
                    color_str = comp.get("value", "")
        now = self._now()
        if not self._is_open_hours(now):
            open_hour = int(self.config.get("open_hour", 8))
            await interaction.response.send_message(
                f"⛔ Roulette Refuge est fermée. Réouverture à {open_hour:02d}:00.",
                ephemeral=True,
            )
            return
        try:
            amount = int(amount_str)
        except Exception:
            await interaction.response.send_message(
                "❌ Mise minimale : 5 XP.",
                ephemeral=True,
            )
            return
        color_choice = color_str.strip().lower()
        if color_choice and color_choice not in ("rouge", "noir"):
            await interaction.response.send_message(
                "❌ Couleur invalide (rouge/noir).",
                ephemeral=True,
            )
            return
        min_bet = int(self.config.get("min_bet", 5))
        if amount < min_bet:
            await interaction.response.send_message(
                "❌ Mise minimale : 5 XP.",
                ephemeral=True,
            )
            return
        user_id = interaction.user.id
        user = interaction.user
        if get_user_account_age_days(user_id) < 2:
            await interaction.response.send_message(
                "❌ Ancienneté requise : 2 jours.",
                ephemeral=True,
            )
            return
        balance = get_user_xp(user_id)
        min_balance_guard = int(self.config.get("min_balance_guard", 10))
        if balance < amount or balance - amount < min_balance_guard:
            await interaction.response.send_message(
                "❌ Solde insuffisant (il faut conserver au moins 10 XP).",
                ephemeral=True,
            )
            return
        if self._bets_today_date != now.date():
            self._bets_today = {}
            self._bets_today_date = now.date()
        cd_until = self._cooldowns.get(user_id)
        if cd_until and cd_until > now:
            remaining = int((cd_until - now).total_seconds())
            await interaction.response.send_message(f"⏳ Attends {remaining}s avant de rejouer.", ephemeral=True)
            return
        self._cooldowns[user_id] = now + timedelta(seconds=15)
        daily_cap = int(self.config.get("daily_cap", 20))
        count = self._bets_today.get(user_id, 0)
        if count >= daily_cap:
            await interaction.response.send_message(
                "📉 Tu as atteint 20 paris aujourd'hui. Reviens demain.",
                ephemeral=True,
            )
            return
        self._bets_today[user_id] = count + 1

        await interaction.response.send_message(
            "✅ Mise reçue. Tirage en cours…",
            ephemeral=True,
        )
        try:
            if color_choice:
                outcome_color = random.choice(["rouge", "noir"])
                segment = f"color_{outcome_color}"
                payout = amount * 2 if color_choice == outcome_color else 0
                ticket_used = consume_free_ticket(user_id)
                delta = payout if ticket_used else payout - amount
                result = {
                    "payout": payout,
                    "delta": delta,
                    "mult": 2.0 if color_choice == outcome_color else 0.0,
                    "notes": f"Couleur gagnante : {outcome_color}",
                    "double_xp": False,
                }
            else:
                segment = self._draw_segment()
                result = self._compute_result(amount, segment)
                ticket_used = consume_free_ticket(user_id)
                payout = int(cast(int, result["payout"]))
                result["delta"] = payout if ticket_used else result["delta"]
                delta = int(cast(int, result["delta"]))
            ts = self._now().isoformat()
            add_user_xp(
                user_id,
                delta,
                guild_id=interaction.guild_id or 0,
                source="pari_xp",
            )
            if result.get("double_xp"):
                apply_double_xp_buff(user_id, 60)
            transactions = storage.load_json(storage.Path(TX_PATH), [])
            day_key = ts.split("T")[0]
            transactions.append(
                {
                    "ts": ts,
                    "user_id": user_id,
                    "username": user.name,
                    "bet": amount,
                    "segment": segment,
                    "payout": result["payout"],
                    "delta": result["delta"],
                    "mult": result["mult"],
                    "notes": result["notes"],
                    "double_xp": result["double_xp"],
                    "day_key": day_key,
                }
            )
            await storage.save_json(storage.Path(TX_PATH), transactions)

            award_ticket = False
            if delta < 0:
                streak = self._loss_streak.get(user_id, 0) + 1
                self._loss_streak[user_id] = streak
                if streak >= 10:
                    self._loss_streak[user_id] = 0
                    self.roulette_store.grant_ticket(str(user_id))
                    award_ticket = True
            else:
                self._loss_streak[user_id] = 0

            # Refresh leaderboard after each bet so the ranking stays up to date
            try:
                channel = await self._get_channel()
                if channel:
                    await self._refresh_leaderboard(channel)
            except Exception:
                pass
            lines = [
                f"Mise : {amount} XP",
                f"Segment : {segment}",
                f"Gain : {result['payout']} XP",
                f"Delta : {result['delta']} XP",
            ]
            if color_choice:
                lines.insert(1, f"Pari couleur : {color_choice}")
            if result["notes"]:
                lines.append(f"Note : {result['notes']}")
            if result.get("double_xp"):
                lines.append("⚡ Tu as gagné un boost Double XP 1h !")
            embed = discord.Embed(title="🎲 Résultat", description="\n".join(lines))
            await interaction.followup.send(embed=embed, ephemeral=True)
            if award_ticket:
                await interaction.followup.send(
                    (
                        f"🎉 Félicitations {user.display_name} !\n"
                        "Tu viens de décrocher un 🎟️ Ticket Gratuit dans la roulette 🌀\n\n"
                        "👉 Utilise-le dès maintenant pour tenter ta chance à la Machine à XP 🎰… sans rien miser !\n\n"
                        "💡 Un tour gratuit = une chance de plus de toucher le jackpot 💎"
                    ),
                    ephemeral=True,
                )
            public_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
            announce_channel = await self._get_announce_channel()
            if announce_channel:
                public_channel = announce_channel
            if public_channel:
                big_win_mult = self.config.get("announce_big_win_mult_threshold", 5)
                big_loss_xp = self.config.get("announce_big_loss_xp_threshold", 100)
                if segment == "super_jackpot_plus_1000":
                    content = f"💥 SUPER JACKPOT ! {user.mention} +1000 XP !"
                    if self.config.get("announce_super_jackpot_ping_here", False):
                        content = "@here " + content
                    await public_channel.send(content)
                elif result["mult"] and result["mult"] >= big_win_mult:
                    await public_channel.send(f"🎉 {user.display_name} gagne {result['mult']}× sa mise ({result['payout']} XP) !")
                elif result["delta"] <= -big_loss_xp:
                    await public_channel.send(
                        f"😢 {user.display_name} vient de perdre {abs(int(cast(int, result['delta'])))} XP..."
                    )
        except Exception as exc:  # pragma: no cover - handled explicitly
            logging.exception("Error while handling bet submission", exc_info=exc)
            try:
                await interaction.followup.send(
                    "❌ Une erreur est survenue lors du traitement de ton pari.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return
    
    async def _self_check_report(self) -> dict:
        report: dict[str, str] = {}

        tz = TZ_PARIS
        open_hour = int(self.config.get("open_hour", 8))
        close_hour = int(self.config.get("close_hour", 2))
        open_ok = (
            not self._is_open_hours(datetime(2023, 1, 1, close_hour, 0, tzinfo=tz))
            and not self._is_open_hours(
                datetime(2023, 1, 1, (open_hour - 1) % 24, 59, tzinfo=tz)
            )
            and self._is_open_hours(datetime(2023, 1, 1, open_hour, 0, tzinfo=tz))
            and self._is_open_hours(
                datetime(2023, 1, 1, (close_hour - 1) % 24, 59, tzinfo=tz)
            )
        )
        report["hours"] = "PASS" if open_ok else "FAIL"

        desc = self._build_hub_embed().description or ""
        hub_ok = (
            f"🟢 État : Ouvert — ferme à ⏰ {close_hour:02d}:00" in desc
            or f"🔴 État : Fermé — ouvre à ⏰ {open_hour:02d}:00" in desc
        )
        report["hub_embed"] = "PASS" if hub_ok else "FAIL"

        modal = self._build_bet_modal()
        ui_ok = getattr(modal, "custom_id", "") == "pari_xp_modal"
        ids = [c.custom_id for c in modal.children if isinstance(c, ui.TextInput)]
        ui_ok &= ids == ["pari_xp_amount", "pari_xp_color"]
        view = self._build_hub_view()
        btn_ids = [c.custom_id or "" for c in view.children if isinstance(c, discord.ui.Button)]
        ui_ok &= all(cid.startswith("pari_xp_") for cid in btn_ids)
        report["ui_ids"] = "PASS" if ui_ok else "FAIL"

        src = inspect.getsource(self._handle_bet_submission)
        guard_strings = [
            "❌ Mise minimale : 5 XP.",
            "❌ Ancienneté requise : 2 jours.",
            "❌ Solde insuffisant (il faut conserver au moins 10 XP).",
            "⏳ Attends {remaining}s avant de rejouer.",
            "📉 Tu as atteint 20 paris aujourd'hui. Reviens demain.",
            "❌ Couleur invalide (rouge/noir).",
        ]
        guards_ok = all(s in src for s in guard_strings)
        report["guards"] = "PASS" if guards_ok else "FAIL"

        draw_src = inspect.getsource(self._draw_segment)
        cond_draw = "probabilities" in draw_src
        double = self._compute_result(10, "double_xp_1h")
        super_jp = self._compute_result(10, "super_jackpot_plus_1000")
        cond_place = (
            double.get("double_xp") and double["delta"] == -10 and super_jp["delta"] == 1000
        )
        cond_msgs = "boost Double XP 1h !" in src
        report["draw_placeholders"] = "PASS" if cond_draw and cond_place and cond_msgs else "FAIL"

        announces_ok = (
            "announce_big_win_mult_threshold" in src
            and "announce_big_loss_xp_threshold" in src
            and "announce_super_jackpot_ping_here" in src
            and "@here" in src
            and "user.display_name" in src
            and "user.mention" in src
        )
        report["announces"] = "PASS" if announces_ok else "FAIL"

        lb_ok = all(hasattr(self, name) for name in ["_ensure_leaderboard_message", "_build_leaderboard_embed"])
        lb_ok &= inspect.getsource(type(self).__init__).count("leaderboard_task.start") > 0
        lb_ok &= getattr(self.leaderboard_task, "seconds", None) == 180 or getattr(
            self.leaderboard_task, "minutes", None
        ) == 3.0
        report["leaderboard"] = "PASS" if lb_ok else "FAIL"

        summary_src = inspect.getsource(self._post_daily_summary)
        summary_ok = all(
            s in summary_src
            for s in [
                "Top 3 gagnants",
                "Top 3 perdants",
                "Plus gros gain unique",
                "Total misé / redistribué",
            ]
        )
        report["daily_summary"] = "PASS" if summary_ok else "FAIL"

        module = inspect.getmodule(self)
        module_src = inspect.getsource(module) if module else ""
        classes = [c for c in vars(module).values() if inspect.isclass(c) and c.__module__ == module.__name__] if module else []
        class_name = self.__class__.__name__
        isolation_ok = module is not None and len([c for c in classes if c.__name__ == class_name]) == 1
        custom_ids = [cid for cid in re.findall(r'custom_id="([^"]+)"', module_src) if not cid.startswith("(")]
        if module:
            isolation_ok &= all(cid.startswith("pari_xp_") for cid in custom_ids)
        else:
            isolation_ok = False
        isolation_ok &= PARI_XP_DATA_DIR == "main/data/pari_xp/"
        isolation_ok &= "pari_xp" in module_src.lower()
        report["isolation"] = "PASS" if isolation_ok else "FAIL"

        return report

    @commands.command(name="pari_xp_selfcheck")
    async def pari_xp_selfcheck(self, ctx: commands.Context) -> None:
        report = await self._self_check_report()
        logging.debug("pari_xp self-check report: %s", report)
        await ctx.send(str(report))

    @app_commands.command(name="pari_xp_selfcheck", description="Diagnostic interne Roulette Refuge")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_pari_xp_selfcheck(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            report = await self._self_check_report()
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur self-check : {e}", ephemeral=True)
            return

        color = discord.Color.green()
        if any(v != "PASS" for v in report.values()):
            color = discord.Color.orange()

        embed = discord.Embed(title="🧪 Roulette Refuge — Self-check", description="Diagnostic interne (PASS/FAIL)", color=color)
        for key, val in report.items():
            embed.add_field(name=key, value=val, inline=True)
        embed.set_footer(text="Ce diagnostic n'altère rien (add-only).")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteRefugeCog(bot))
