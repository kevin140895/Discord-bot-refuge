import discord
from discord.ext import commands, tasks  # noqa: F401
from datetime import datetime
from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from discord import ui
from utils.storage import load_json  # noqa: F401
from utils.xp_adapter import get_user_xp, get_user_account_age_days

from utils import storage, timezones

DATA_DIR = "main/data/pari_xp/"
CONFIG_PATH = DATA_DIR + "config.json"
STATE_PATH = DATA_DIR + "state.json"
LB_PATH = DATA_DIR + "leaderboard.json"


class RouletteRefugeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = storage.load_json(storage.Path(CONFIG_PATH), {})
        self.state = storage.load_json(storage.Path(STATE_PATH), {})
        self.scheduler_task.start()
        self._cooldowns: dict[int, datetime] = {}
        self._bets_today: dict[int, int] = {}
        self._bets_today_date = self._now().date()

    def _now(self) -> datetime:
        return datetime.now(timezones.TZ_PARIS)

    def _is_open_hours(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or self._now()
        return dt.hour >= 8 or dt.hour < 2

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

    async def _ensure_hub_message(self, channel: discord.TextChannel) -> None:
        hub_id = self.state.get("hub_message_id")
        embed = self._build_hub_embed()
        view = self._build_hub_view()
        for child in view.children:
            if isinstance(child, discord.ui.Button) and getattr(child, "custom_id", None) == "pari_xp_bet":
                child.callback = self._bet_button_callback
        message = None
        if hub_id:
            try:
                message = await channel.fetch_message(int(hub_id))
            except Exception:
                message = None
        if message:
            await message.edit(embed=embed, view=view)
        else:
            message = await channel.send(embed=embed, view=view)
            self.state["hub_message_id"] = message.id
            await storage.save_json(storage.Path(STATE_PATH), self.state)

    def _build_hub_embed(self) -> discord.Embed:
        title = self.config.get("game_display_name", "🤑 Roulette Refuge")
        lines = [
            "💵 **Mise min**: 5 XP · 🛑 **Cooldown**: 15s · 🎲 **Cap**: 20/jour",
            "Résultats privés (éphémères). Gros événements annoncés publiquement.",
            "—",
            "État : "
            + (
                "🟢 **Ouvert — ferme à 02:00**"
                if self._is_open_hours()
                else "🔴 **Fermé — ouvre à 08:00**"
            ),
        ]
        return discord.Embed(title=title, description="\n".join(lines))

    def _build_hub_view(self) -> discord.ui.View:
        cog = self

        class HubView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=None)

            @discord.ui.button(
                custom_id="pari_xp_bet",
                label="Miser XP",
                style=discord.ButtonStyle.success,
            )
            async def bet(  # type: ignore[override]
                self, interaction: discord.Interaction, button: discord.ui.Button
            ) -> None:
                await interaction.response.send_message(
                    "Modal non implémentée.", ephemeral=True
                )

            @discord.ui.button(
                custom_id="pari_xp_leaderboard",
                label="📊 Leaderboard",
                style=discord.ButtonStyle.primary,
            )
            async def leaderboard(  # type: ignore[override]
                self, interaction: discord.Interaction, button: discord.ui.Button
            ) -> None:
                msg_id = cog.state.get("leaderboard_message_id")
                if msg_id:
                    url = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{msg_id}"
                    await interaction.response.send_message(url, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "indisponible", ephemeral=True
                    )

        return HubView()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.state = storage.load_json(storage.Path(STATE_PATH), {})
        channel = await self._get_channel()
        if channel:
            await self._ensure_hub_message(channel)
            # leaderboard sera géré aux étapes suivantes

    async def _update_hub_state(self, is_open: bool) -> None:
        self.state["is_open"] = is_open
        await storage.save_json(storage.Path(STATE_PATH), self.state)
        channel = await self._get_channel()
        if channel:
            await self._ensure_hub_message(channel)

    async def _announce_open(self, channel: discord.TextChannel) -> None:
        lines = [
            "Horaires : 08:00→02:00",
            "Mise min : 5 XP",
            "Cooldown : 15s",
            "Cap : 20/jour",
        ]
        embed = discord.Embed(
            title="🤑 Roulette Refuge — Ouverture",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        await channel.send(embed=embed)

    async def _announce_close(self, channel: discord.TextChannel) -> None:
        embed = discord.Embed(
            title="🤑 Roulette Refuge — Clôture du jour",
            description="(placeholder)",
            color=discord.Color.red(),
        )
        await channel.send(embed=embed)

    @tasks.loop(minutes=1.0)
    async def scheduler_task(self) -> None:
        tz = getattr(timezones, "TZ_PARIS", ZoneInfo("Europe/Paris"))
        now = datetime.now(tz)
        open_hour = self.config.get("open_hour", 8)
        last_call_hour = self.config.get("last_call_hour", 1)
        last_call_minute = self.config.get("last_call_minute", 45)
        close_hour = self.config.get("close_hour", 2)

        if now.hour == 0 and now.minute == 0:
            self.state["daily_cap_counter"] = 0
            await storage.save_json(storage.Path(STATE_PATH), self.state)

        channel = await self._get_channel()
        if not channel:
            return

        if now.hour == open_hour and now.minute == 0:
            await self._announce_open(channel)
            await self._update_hub_state(True)
        elif now.hour == last_call_hour and now.minute == last_call_minute:
            await channel.send(
                "⏳ Dernier appel — fermeture dans 15 minutes (02:00)."
            )
        elif now.hour == close_hour and now.minute == 0:
            await self._announce_close(channel)
            await self._update_hub_state(False)

    @scheduler_task.before_loop
    async def _wait_ready_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    async def _bet_button_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(self._build_bet_modal())

    def _build_bet_modal(self) -> ui.Modal:
        cog = self

        class BetModal(ui.Modal):
            def __init__(self) -> None:
                super().__init__(
                    title="🤑 Roulette Refuge — Parier", custom_id="pari_xp_modal"
                )
                self.amount = ui.TextInput(
                    label="Montant (XP)",
                    placeholder="≥ 5",
                    required=True,
                    custom_id="amount",
                )
                self.use_ticket = ui.TextInput(
                    label="Utiliser un ticket gratuit ? (oui/non)",
                    placeholder="non",
                    required=False,
                    custom_id="use_ticket",
                )
                self.add_item(self.amount)
                self.add_item(self.use_ticket)

            async def on_submit(
                self, interaction: discord.Interaction
            ) -> None:  # type: ignore[override]
                await cog._handle_bet_submission(
                    interaction, self.amount.value, self.use_ticket.value or ""
                )

        return BetModal()

    async def _handle_bet_submission(
        self,
        interaction: discord.Interaction,
        amount_str: str,
        use_ticket_str: str,
    ) -> None:
        now = self._now()
        if not self._is_open_hours(now):
            await interaction.response.send_message(
                "⛔ Roulette Refuge est fermée. Réouverture à 08:00.",
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
        min_bet = int(self.config.get("min_bet", 5))
        if amount < min_bet:
            await interaction.response.send_message(
                "❌ Mise minimale : 5 XP.",
                ephemeral=True,
            )
            return
        user_id = interaction.user.id
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
            await interaction.response.send_message(
                f"⏳ Attends {remaining}s avant de rejouer.", ephemeral=True
            )
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
        _ = use_ticket_str.lower() == "oui"
        await interaction.response.send_message(
            "✅ Mise reçue. (Tirage à l'étape 6)", ephemeral=True
        )

