from __future__ import annotations

import json
import logging
import re
import typing

import discord
from discord.ext import commands, tasks

from datetime import datetime, timedelta, timezone

from storage.economy import (
    ECONOMY_DIR,
    SHOP_FILE,
    load_boosts,
    load_tickets,
    load_ui,
    save_boosts,
    save_tickets,
    save_ui,
    transactions,
)
from utils import xp_adapter
import config

CHANNEL_ID = 1409633293791400108

logger = logging.getLogger(__name__)


class ShopView(discord.ui.View):
    """Vue persistante pour la boutique."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Ticket Royal",
                style=discord.ButtonStyle.green,
                custom_id="shop_buy:ticket_royal",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Double XP 1h",
                style=discord.ButtonStyle.green,
                custom_id="shop_buy:double_xp_1h",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="VIP 24h",
                style=discord.ButtonStyle.green,
                custom_id="shop_buy:vip_24h",
            )
        )


class BankTransferModal(discord.ui.Modal):
    """Modal de virement bancaire."""

    amount = discord.ui.TextInput(label="Montant")
    beneficiary = discord.ui.TextInput(label="Bénéficiaire ID")

    def __init__(self) -> None:
        super().__init__(title="Virement")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        logger.info(
            "Validation modal virement par %s: montant=%s, beneficiaire=%s",
            interaction.user.id,
            self.amount.value,
            self.beneficiary.value,
        )

        # Parse amount
        try:
            amount = int(self.amount.value)
        except (TypeError, ValueError):
            logger.warning("Montant invalide: %s", self.amount.value)
            await interaction.response.send_message(
                "Montant invalide.", ephemeral=True
            )
            return

        # Parse beneficiary ID (supports mention or raw ID)
        try:
            beneficiary_id = int(
                re.sub(r"[^0-9]", "", self.beneficiary.value.strip())
            )
        except ValueError:
            logger.warning(
                "Beneficiaire invalide: %s", self.beneficiary.value
            )
            await interaction.response.send_message(
                "Bénéficiaire invalide.", ephemeral=True
            )
            return

        if amount <= 0:
            logger.info("Montant non positif: %s", amount)
            await interaction.response.send_message(
                "Le montant doit être supérieur à 0.", ephemeral=True
            )
            return

        if beneficiary_id == interaction.user.id:
            logger.info("Transfert vers soi-même refusé (%s)", beneficiary_id)
            await interaction.response.send_message(
                "Vous ne pouvez pas vous envoyer des XP.", ephemeral=True
            )
            return

        balance = xp_adapter.get_balance(interaction.user.id)
        if balance < amount:
            logger.info(
                "Solde insuffisant pour %s: %s < %s",
                interaction.user.id,
                balance,
                amount,
            )
            await interaction.response.send_message(
                "Solde insuffisant.", ephemeral=True
            )
            return

        logger.info(
            "Début virement: %s -> %s (%s XP)",
            interaction.user.id,
            beneficiary_id,
            amount,
        )

        await xp_adapter.add_xp(
            interaction.user.id,
            amount=-amount,
            guild_id=interaction.guild_id or 0,
            source="bank_transfer",
        )
        await xp_adapter.add_xp(
            beneficiary_id,
            amount=amount,
            guild_id=interaction.guild_id or 0,
            source="bank_transfer",
        )

        timestamp = datetime.now(timezone.utc).isoformat()
        await transactions.add(
            {
                "type": "gift",
                "user_id": interaction.user.id,
                "to": beneficiary_id,
                "amount": amount,
                "timestamp": timestamp,
            }
        )
        await transactions.add(
            {
                "type": "receive",
                "user_id": beneficiary_id,
                "from": interaction.user.id,
                "amount": amount,
                "timestamp": timestamp,
            }
        )

        await interaction.response.send_message(
            "🏦 Virement envoyé !", ephemeral=True
        )
        logger.info("Virement effectué")

        # Try to DM the beneficiary
        recipient: typing.Optional[discord.abc.User] = None
        if interaction.guild:
            recipient = interaction.guild.get_member(beneficiary_id)
        if recipient is None:
            recipient = interaction.client.get_user(beneficiary_id)
        if recipient is None:
            try:  # pragma: no cover - network
                recipient = await interaction.client.fetch_user(beneficiary_id)
            except Exception:  # pragma: no cover - best effort
                pass

        if recipient is not None:
            try:
                await recipient.send(
                    f"🏦 Vous venez de recevoir {amount} XP de la part de {interaction.user.mention}."
                )
                logger.info("DM envoyé à %s", beneficiary_id)
            except Exception:  # pragma: no cover - best effort
                logger.warning(
                    "Échec de l'envoi du DM à %s", beneficiary_id, exc_info=True
                )
                await interaction.followup.send(
                    f"Impossible d'envoyer un DM à <@{beneficiary_id}>.",
                    ephemeral=True,
                )
        else:  # pragma: no cover - best effort
            logger.warning(
                "Bénéficiaire %s introuvable pour DM", beneficiary_id
            )
            await interaction.followup.send(
                f"Impossible de contacter <@{beneficiary_id}>.",
                ephemeral=True,
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
        logger.info(
            "Ouverture modal virement par %s", interaction.user.id
        )
        await interaction.response.send_modal(BankTransferModal())


class EconomyUICog(commands.Cog):
    """Gère les vues persistance de l'économie (boutique et banque)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shop_view = ShopView()
        self.bank_view = BankView()

    @tasks.loop(minutes=5)
    async def boosts_cleanup(self) -> None:
        await self._cleanup_boosts_once()

    @boosts_cleanup.before_loop
    async def before_boosts_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    async def _cleanup_boosts_once(self) -> None:
        try:
            boosts = load_boosts()
        except Exception as e:
            logger.warning("Lecture boosts.json échouée: %s", e)
            return

        now = datetime.now(timezone.utc)
        changed = False
        guild = self.bot.get_guild(getattr(config, "GUILD_ID", 0))

        for uid, entries in list(boosts.items()):
            new_entries = []
            for entry in entries:
                until_str = entry.get("until")
                try:
                    until = datetime.fromisoformat(until_str)
                except Exception:
                    changed = True
                    continue
                if until <= now:
                    changed = True
                    role_id = int(entry.get("role_id", 0))
                    if entry.get("type") == "vip" and not role_id:
                        role_id = getattr(config, "VIP_24H_ROLE_ID", 0)
                    if role_id and guild:
                        member = guild.get_member(int(uid))
                        role = guild.get_role(role_id)
                        if member and role:
                            try:
                                await member.remove_roles(
                                    role, reason="Boost expiré"
                                )
                            except Exception:  # pragma: no cover - best effort
                                logger.warning(
                                    "Impossible de retirer le rôle %s de %s",
                                    role_id,
                                    uid,
                                    exc_info=True,
                                )
                else:
                    new_entries.append(entry)
            if new_entries:
                boosts[uid] = new_entries
            else:
                boosts.pop(uid, None)

        if changed:
            await save_boosts(boosts)

    async def cog_load(self) -> None:  # pragma: no cover - requires discord context
        logger.info("Chargement de l'interface économie")
        self.boosts_cleanup.start()
        ECONOMY_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ui_data = load_ui()
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("Lecture ui.json échouée: %s", e)
            ui_data = {}
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
            await save_ui(ui_data)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("Écriture ui.json échouée: %s", e)

    def cog_unload(self) -> None:
        self.boosts_cleanup.cancel()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        custom_id = getattr(getattr(interaction, "data", {}), "get", lambda _key: None)(
            "custom_id"
        )
        if not isinstance(custom_id, str) or not custom_id.startswith("shop_buy:"):
            return
        item_key = custom_id.split(":", 1)[1]
        await self._handle_shop_purchase(interaction, item_key)

    async def _handle_shop_purchase(
        self, interaction: discord.Interaction, item_key: str
    ) -> None:
        try:
            shop = json.loads(SHOP_FILE.read_text(encoding="utf-8"))
        except Exception:
            await interaction.response.send_message(
                "Boutique indisponible.", ephemeral=True
            )
            return
        item = shop.get(item_key)
        if not item:
            await interaction.response.send_message("Article inconnu.", ephemeral=True)
            return
        price = int(item.get("price", 0))
        user_id = interaction.user.id
        balance = xp_adapter.get_balance(user_id)
        if balance < price:
            await interaction.response.send_message(
                "Solde insuffisant.", ephemeral=True
            )
            return
        await xp_adapter.add_xp(
            user_id,
            amount=-price,
            guild_id=interaction.guild_id or 0,
            source="shop",
        )

        if item_key == "ticket_royal":
            tickets = load_tickets()
            key = str(user_id)
            tickets[key] = int(tickets.get(key, 0)) + 1
            await save_tickets(tickets)
        elif item_key == "double_xp_1h":
            boosts = load_boosts()
            key = str(user_id)
            boost_list = boosts.setdefault(key, [])
            until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            boost_list.append({"type": "double_xp", "until": until})
            await save_boosts(boosts)
        elif item_key == "vip_24h":
            boosts = load_boosts()
            key = str(user_id)
            boost_list = boosts.setdefault(key, [])
            until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            boost_list.append({"type": "vip", "until": until})
            await save_boosts(boosts)
            role_id = getattr(config, "VIP_24H_ROLE_ID", 0)
            if role_id and interaction.guild:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await interaction.user.add_roles(
                            role, reason="Achat VIP 24h"
                        )
                    except Exception:  # pragma: no cover - best effort
                        logger.warning("Impossible d'ajouter le rôle VIP", exc_info=True)

        await transactions.add(
            {
                "type": "buy",
                "user_id": user_id,
                "item": item_key,
                "price": price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        await interaction.response.send_message(
            f"Achat de {item.get('name', item_key)} effectué !", ephemeral=True
        )

    async def _ensure_message(
        self,
        channel: discord.TextChannel,
        message_id: typing.Optional[int],
        content: str,
        view: discord.ui.View,
        label: str,
    ) -> typing.Optional[int]:
        msg: typing.Optional[discord.Message] = None
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

