from __future__ import annotations

import json
import logging
import typing

import discord
from discord.ext import commands, tasks

from datetime import datetime, timezone

from storage.economy import (
    ECONOMY_DIR,
    SHOP_FILE,
    get_boost_lock,
    get_ticket_lock,
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

CHANNEL_ID = config.ECONOMY_CHANNEL_ID

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

SHOP_ACCENT = discord.Colour.gold()
SHOP_PRESENTATION: dict[str, dict[str, str]] = {
    "ticket_royal": {
        "emoji": "🎟️",
        "description": "Une tentative supplémentaire à la Machine à sous.",
        "limit_label": "Stock maximum",
        "verb": "Acheter",
    },
    "double_xp_1h": {
        "emoji": "⚡",
        "description": "Multiplie tes gains d'XP personnels pendant **60 minutes**.",
        "limit_label": "Maximum actif",
        "verb": "Activer",
    },
}


def _activate_personal_double_xp(user_id: int, duration_minutes: int) -> datetime:
    """Active ou prolonge le vrai bonus Double XP utilisé par ``award_xp``.

    Le module XP est résolu au moment de l'achat, et non lors du chargement de
    ``economy_ui``. Cela évite de conserver une référence vers une ancienne
    instance du module si Discord charge ensuite ``cogs.xp`` comme extension.

    ``cogs.xp.add_xp_boost`` remplace normalement l'expiration existante. Pour
    les achats boutique, on conserve le temps restant puis on ajoute la nouvelle
    durée afin que deux achats d'une heure donnent bien deux heures de bonus au
    total au lieu de faire payer deux fois pour une seule heure.
    """
    from cogs import xp as xp_cog

    now = datetime.now(timezone.utc)
    current_expiry = xp_cog.XP_BOOSTS.get(str(user_id))
    remaining_minutes = 0.0
    if current_expiry and current_expiry > now:
        remaining_minutes = (current_expiry - now).total_seconds() / 60.0

    xp_cog.add_xp_boost(user_id, duration_minutes + remaining_minutes)
    return xp_cog.XP_BOOSTS[str(user_id)]


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


def _shop_item_is_visible(key: str, item: dict[str, typing.Any]) -> bool:
    name = str(item.get("name", key))
    return "vip" not in key.lower() and "vip" not in name.lower()


class ShopPurchaseButton(discord.ui.Button):
    """Persistent purchase button used as a Components V2 section accessory."""

    def __init__(
        self,
        cog: "EconomyUICog",
        *,
        item_key: str,
        label: str,
        emoji: str,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.success,
            custom_id=f"shop_buy:{item_key}",
        )
        self.cog = cog
        self.item_key = item_key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog._handle_shop_purchase(interaction, self.item_key)


class ShopView(discord.ui.LayoutView):
    """Vue Components V2 persistante pour la boutique."""

    def __init__(self, cog: "EconomyUICog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(self._build_container())

    def _build_container(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=SHOP_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🛒 BOUTIQUE DU REFUGE\n"
                "Dépense tes XP pour obtenir des avantages utilisables dans le Refuge."
            )
        )

        data = _load_shop()
        if not data:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "⚠️ **Boutique indisponible.**\n"
                    "Le catalogue n'a pas pu être chargé."
                )
            )
            return container

        visible_items = [
            (key, item)
            for key, item in data.items()
            if isinstance(item, dict) and _shop_item_is_visible(key, item)
        ]

        for key, item in visible_items:
            container.add_item(discord.ui.Separator())
            name = str(item.get("name", key))
            price = item.get("price")
            price_text = f"{price} XP" if price else "Prix non défini"
            limit = PURCHASE_LIMITS.get(key)
            presentation = SHOP_PRESENTATION.get(key)

            if presentation is None:
                limit_text = f"\nLimite : **{limit}**" if limit is not None else ""
                container.add_item(
                    discord.ui.TextDisplay(
                        f"### 🧩 {name}\n**{price_text}**{limit_text}"
                    )
                )
                continue

            limit_text = ""
            if limit is not None:
                limit_text = f"\n`{presentation['limit_label']} : {limit}`"
            details = discord.ui.TextDisplay(
                f"### {presentation['emoji']} {name.upper()}\n"
                f"**{price_text}**\n"
                f"{presentation['description']}"
                f"{limit_text}"
            )
            button_label = presentation["verb"]
            if price:
                button_label = f"{button_label} · {price_text}"
            container.add_item(
                discord.ui.Section(
                    details,
                    accessory=ShopPurchaseButton(
                        self.cog,
                        item_key=key,
                        label=button_label,
                        emoji=presentation["emoji"],
                    ),
                )
            )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "### 💰 Fonctionnement\n"
                "Tes achats utilisent directement ton solde XP.\n"
                "Les limites de stockage et d'activation sont appliquées automatiquement."
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                "-# Les achats sont définitifs · Le solde est vérifié au moment de la transaction."
            )
        )
        return container


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
        role_removals: list[tuple[typing.Any, typing.Any, str, int]] = []

        async with get_boost_lock():
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
                                role_removals.append((member, role, uid, role_id))
                    else:
                        new_entries.append(entry)
                if new_entries:
                    boosts[uid] = new_entries
                else:
                    boosts.pop(uid, None)

            if changed:
                await save_boosts(boosts)

        # Discord calls are intentionally performed after the persisted boost
        # state is committed and the shared boost lock has been released.
        for member, role, uid, role_id in role_removals:
            try:
                await member.remove_roles(role, reason="Boost expiré")
            except Exception:  # pragma: no cover - best effort
                logger.warning(
                    "Impossible de retirer le rôle %s de %s",
                    role_id,
                    uid,
                    exc_info=True,
                )

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
            None,
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
        self,
        interaction: discord.Interaction,
        item_key: str,
        *,
        _ticket_locked: bool = False,
        _boost_locked: bool = False,
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
        if item_key == "ticket_royal" and not _ticket_locked:
            async with get_ticket_lock():
                await self._handle_shop_purchase(
                    interaction,
                    item_key,
                    _ticket_locked=True,
                    _boost_locked=_boost_locked,
                )
            return
        if item_key == "double_xp_1h" and not _boost_locked:
            async with get_boost_lock():
                await self._handle_shop_purchase(
                    interaction,
                    item_key,
                    _ticket_locked=_ticket_locked,
                    _boost_locked=True,
                )
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
        try:
            await xp_adapter.add_xp(
                user_id,
                amount=-price,
                guild_id=interaction.guild_id or 0,
                source="shop",
            )
        except xp_adapter.InsufficientXPError:
            # Le pré-contrôle de solde est uniquement informatif. Le débit
            # atomique reste la source de vérité si deux achats/paris arrivent
            # simultanément pour le même utilisateur.
            await interaction.response.send_message(
                "Solde insuffisant.", ephemeral=True
            )
            return

        if item_key == "ticket_royal":
            tickets = load_tickets()
            key = str(user_id)
            tickets[key] = int(tickets.get(key, 0)) + 1
            await save_tickets(tickets)
        elif item_key == "double_xp_1h":
            boosts = load_boosts()
            key = str(user_id)
            boost_list = boosts.setdefault(key, [])
            expiry = _activate_personal_double_xp(user_id, 60)
            boost_list.append({"type": "double_xp", "until": expiry.isoformat()})
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
        content: typing.Optional[str],
        view: typing.Union[discord.ui.View, discord.ui.LayoutView],
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
                msg = await channel.send(content=content, view=view)
                await msg.pin(reason=f"UI {label}")
                logger.info("%s: message créé (%s)", label, msg.id)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("%s: création impossible (%s)", label, e)
                return None
        else:
            try:
                if isinstance(view, discord.ui.LayoutView):
                    await msg.edit(
                        content=None,
                        embeds=[],
                        attachments=[],
                        view=view,
                    )
                else:
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
