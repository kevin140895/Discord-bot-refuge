from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands

CHANNEL_ID = 1409633293791400108
DATA_DIR = Path(__file__).parent.parent / "data" / "economy"
UI_FILE = DATA_DIR / "ui.json"
SHOP_FILE = DATA_DIR / "shop.json"

logger = logging.getLogger(__name__)


class ShopView(discord.ui.View):
    """Vue persistante pour la boutique."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket Royal",
        style=discord.ButtonStyle.green,
        custom_id="shop_buy:ticket_royal",
    )
    async def buy_ticket_royal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "🎟️ Ticket Royal acheté !", ephemeral=True
        )

    @discord.ui.button(
        label="Double XP 1h",
        style=discord.ButtonStyle.green,
        custom_id="shop_buy:double_xp_1h",
    )
    async def buy_double_xp(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "⚡ Bonus XP 1h acheté !", ephemeral=True
        )

    @discord.ui.button(
        label="VIP 24h",
        style=discord.ButtonStyle.green,
        custom_id="shop_buy:vip_24h",
    )
    async def buy_vip(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "👑 VIP 24h acheté !", ephemeral=True
        )


class BankTransferModal(discord.ui.Modal, title="Virement"):
    user = discord.ui.TextInput(label="Utilisateur ID")
    amount = discord.ui.TextInput(label="Montant")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "🏦 Virement envoyé !", ephemeral=True
        )


class BankView(discord.ui.View):
    """Vue persistante pour la banque."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Faire un virement",
        style=discord.ButtonStyle.primary,
        custom_id="bank_transfer_open",
    )
    async def open_transfer(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(BankTransferModal())


class EconomyUICog(commands.Cog):
    """Gère les vues persistance de l'économie (boutique et banque)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shop_view = ShopView()
        self.bank_view = BankView()

    async def cog_load(self) -> None:  # pragma: no cover - requires discord context
        logger.info("Chargement de l'interface économie")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ui_data: dict[str, Any] = {}
        if UI_FILE.exists():
            try:
                ui_data = json.loads(UI_FILE.read_text(encoding="utf-8"))
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("Lecture ui.json échouée: %s", e)
        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Salon économie introuvable (%s)", CHANNEL_ID)
            return

        self.bot.add_view(self.shop_view)
        self.bot.add_view(self.bank_view)

        shop_id = await self._ensure_message(
            channel,
            ui_data.get("shop_message_id"),
            self._build_shop_text(),
            self.shop_view,
            "Boutique",
        )
        if shop_id:
            ui_data["shop_message_id"] = shop_id

        bank_id = await self._ensure_message(
            channel,
            ui_data.get("bank_message_id"),
            self._bank_text(),
            self.bank_view,
            "Banque",
        )
        if bank_id:
            ui_data["bank_message_id"] = bank_id

        try:
            UI_FILE.write_text(
                json.dumps(ui_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("Écriture ui.json échouée: %s", e)

    async def _ensure_message(
        self,
        channel: discord.TextChannel,
        message_id: Optional[int],
        content: str,
        view: discord.ui.View,
        label: str,
    ) -> Optional[int]:
        msg: Optional[discord.Message] = None
        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
            except Exception:  # pragma: no cover - network errors
                logger.info("%s: ancien message introuvable", label)
        if msg is None:
            try:
                msg = await channel.send(content, view=view)
                await msg.pin(reason=f"UI {label}")
                logger.info("%s: message créé (%s)", label, msg.id)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("%s: création impossible (%s)", label, e)
                return None
        else:
            try:
                await msg.edit(content=content, view=view)
                logger.info("%s: message mis à jour", label)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("%s: mise à jour impossible (%s)", label, e)
        return getattr(msg, "id", None)

    def _build_shop_text(self) -> str:
        try:
            data = json.loads(SHOP_FILE.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("Lecture shop.json échouée: %s", e)
            return "Boutique indisponible."
        lines = ["🛒 **Boutique du Refuge**"]
        for key, item in data.items():
            name = item.get("name", key)
            price = item.get("price")
            lines.append(f"- **{name}** – {price}💰" if price else f"- **{name}**")
        return "\n".join(lines)

    def _bank_text(self) -> str:
        return (
            "🏦 **Banque du Refuge**\n"
            "Utilise le bouton ci-dessous pour transférer tes crédits."
        )


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - requires discord
    await bot.add_cog(EconomyUICog(bot))

