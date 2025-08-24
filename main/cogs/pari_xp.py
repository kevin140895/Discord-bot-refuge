import discord
from discord.ext import commands, tasks  # noqa: F401
from datetime import datetime
from typing import Optional

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

