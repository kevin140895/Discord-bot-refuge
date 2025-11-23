from __future__ import annotations

import json
import logging
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

DEFAULT_SHOP: dict[str, dict[str, typing.Any]] = {
    "ticket_royal": {"name": "Ticket Royal", "price": 500},
    "double_xp_1h": {"name": "Double XP 1h", "price": 300},
}

# Limites d'achats par utilisateur pour certains articles
PURCHASE_LIMITS: dict[str, int] = {
    "ticket_royal": 3,
    "double_xp_1h": 2,
}


def _load_shop() -> typing.Optional[dict[str, typing.Any]]:
    try:
        return json.loads(SHOP_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("Lecture shop.json échouée: %s", e)
        try:
            SHOP_FILE.write_text(
                json.dumps(DEFAULT_SHOP, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("shop.json créé avec le contenu par défaut")
            return DEFAULT_SHOP.copy()
        except Exception as e2:  # pragma: no cover - best effort
            logger.error("Création de shop.json impossible: %s", e2)
            return None


class ShopView(discord.ui.View):
    """Vue persistante pour la boutique."""

    def __init__(self, cog: "EconomyUICog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Ticket Royal",
        style=discord.ButtonStyle.green,
        custom_id="shop_buy:ticket_royal",
    )
    async def ticket_royal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog._handle_shop_purchase(interaction, "ticket_royal")

    @discord.ui.button(
        label="Double XP 1h",
        style=discord.ButtonStyle.green,
        custom_id="shop_buy:double_xp_1h",
    )
    async def double_xp_1h(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog._handle_shop_purchase(interaction, "double_xp_1h")




class EconomyUICog(commands.Cog):
    """Gère les vues persistantes de l'économie (boutique)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.shop_view = ShopView(self)

    @tasks.loop(minutes=5)
    async def boosts_cleanup(self) -> None:
        try:
            await self._cleanup_boosts_once()
        except Exception:
            logger.exception("Erreur dans boosts_cleanup")

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
        try:
            channel = getattr(self.bot, "get_channel", lambda _cid: None)(CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(CHANNEL_ID)  # type: ignore[attr-defined]
        except discord.NotFound:
            logger.warning("Salon économie introuvable (%s)", CHANNEL_ID)
            return
        except discord.Forbidden:
            logger.warning(
                "Accès refusé au salon économie (%s)", CHANNEL_ID
            )
            return
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Salon économie introuvable (%s)", CHANNEL_ID)
            return

        self.bot.add_view(self.shop_view)

        shop_id = await self._ensure_message(
            channel,
            ui_data.get("shop_message_id"),
            self._build_shop_text(),
            self.shop_view,
            "Boutique",
        )
        if shop_id:
            ui_data["shop_message_id"] = shop_id

        try:
            await save_ui(ui_data)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("Écriture ui.json échouée: %s", e)

    def cog_unload(self) -> None:
        self.boosts_cleanup.cancel()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        data = interaction.data if isinstance(getattr(interaction, "data", None), dict) else {}
        custom_id = data.get("custom_id")
        if not isinstance(custom_id, str) or not custom_id.startswith("shop_buy:"):
            return
        response = getattr(interaction, "response", None)
        if callable(getattr(response, "is_done", None)) and response.is_done():
            return
        item_key = custom_id.split(":", 1)[1]
        await self._handle_shop_purchase(interaction, item_key)

    async def _handle_shop_purchase(
        self, interaction: discord.Interaction, item_key: str
    ) -> None:
        shop = _load_shop()
        if not shop:
            await interaction.response.send_message(
                "Boutique indisponible.", ephemeral=True
            )
            return
        item = shop.get(item_key)
        if not item or "vip" in item_key.lower() or "vip" in str(
            item.get("name", "")
        ).lower():
            await interaction.response.send_message("Article inconnu.", ephemeral=True)
            return
        user_id = interaction.user.id
        limit = PURCHASE_LIMITS.get(item_key)
        if limit is not None:
            if item_key == "ticket_royal":
                tickets = load_tickets()
                count = int(tickets.get(str(user_id), 0))
                if count >= limit:
                    await interaction.response.send_message(
                        (
                            f"Tu as déjà {count} Ticket Royal en stock "
                            f"(max {limit}). Utilise-les avant d'en racheter."
                        ),
                        ephemeral=True,
                    )
                    return
            elif item_key == "double_xp_1h":
                boosts = load_boosts()
                now = datetime.now(timezone.utc)
                active_boosts = 0
                for entry in boosts.get(str(user_id), []):
                    if entry.get("type") != "double_xp":
                        continue
                    until_str = entry.get("until")
                    try:
                        until = datetime.fromisoformat(until_str)
                    except Exception:
                        continue
                    if until > now:
                        active_boosts += 1
                if active_boosts >= limit:
                    await interaction.response.send_message(
                        (
                            "Tu as déjà atteint la limite de boosts Double XP actifs "
                            f"(max {limit}). Attends leur expiration avant d'en racheter."
                        ),
                        ephemeral=True,
                    )
                    return
            else:
                txs = await transactions.all()
                count = sum(
                    1
                    for tx in txs
                    if tx.get("type") == "buy"
                    and tx.get("user_id") == user_id
                    and tx.get("item") == item_key
                )
                if count >= limit:
                    await interaction.response.send_message(
                        f"Vous avez atteint la limite d'achat pour {item.get('name', item_key)} (max {limit}).",
                        ephemeral=True,
                    )
                    return
        price = int(item.get("price", 0))
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
        data = _load_shop()
        if not data:
            return "Boutique indisponible."
        lines = ["🛒 **Boutique du Refuge**"]
        for key, item in data.items():
            name = item.get("name", key)
            if "vip" in key.lower() or "vip" in name.lower():
                continue
            price = item.get("price")
            limit = PURCHASE_LIMITS.get(key)
            limit_txt = f" (max {limit})" if limit is not None else ""
            lines.append(
                f"- **{name}** – {price}💰{limit_txt}" if price else f"- **{name}**{limit_txt}"
            )
        return "\n".join(lines)


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - requires discord
    await bot.add_cog(EconomyUICog(bot))

