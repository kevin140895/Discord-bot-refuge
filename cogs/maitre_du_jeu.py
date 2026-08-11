"""Assistant Maître du jeu déclenché par une mention directe du bot.

La V2 enrichit la V1 avec des réponses contextuelles en lecture seule : état XP,
Double XP personnel, catalogue boutique et état radio. Aucun chemin de cette cog
n'exécute de commande métier ni ne modifie l'économie, l'XP ou la radio.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from enum import Enum

import discord
from discord.ext import commands

from config import (
    RADIO_RAP_FR_STREAM_URL,
    RADIO_RAP_STREAM_URL,
    RADIO_STREAM_URL,
    ROCK_RADIO_STREAM_URL,
)
from storage.economy import SHOP_FILE
from storage.xp_store import xp_store
from utils.persistence import read_json_safe


class AssistantIntent(str, Enum):
    """Intentions prises en charge par le Maître du jeu."""

    HELP = "help"
    XP = "xp"
    BOOST = "boost"
    SHOP = "shop"
    RADIO = "radio"
    COMMANDS = "commands"
    DIAGNOSTIC = "diagnostic"
    UNKNOWN = "unknown"


def _normalize_text(value: str) -> str:
    """Normalise une question pour une détection simple et robuste."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(without_accents.split())


def extract_question(content: str, bot_user_id: int) -> str:
    """Retire les formes de mention Discord du bot et renvoie la question."""

    mention_pattern = rf"<@!?{bot_user_id}>"
    return re.sub(mention_pattern, " ", content).strip(" \t\n,:;-")


def classify_question(question: str) -> AssistantIntent:
    """Classe une question dans le périmètre déterministe de l'assistant."""

    text = _normalize_text(question)
    if not text:
        return AssistantIntent.HELP

    diagnostic_markers = (
        "pourquoi je n ai pas gagne",
        "pourquoi j ai pas gagne",
        "pourquoi je n ai pas recu",
        "pourquoi j ai pas recu",
        "pas gagne d xp",
        "pas recu d xp",
        "aucun xp",
    )
    if any(marker in text for marker in diagnostic_markers):
        return AssistantIntent.DIAGNOSTIC

    # Les intentions d'achat/prix passent avant Double XP :
    # « où acheter un double XP ? » doit être traité comme une question boutique.
    shop_markers = ("boutique", "acheter", "achat", "ticket royal", "ticket")
    product_markers = ("double xp", "boost", "ticket")
    price_markers = ("prix", "coute", "combien vaut", "combien coute")
    if any(token in text for token in shop_markers) or (
        any(token in text for token in product_markers)
        and any(token in text for token in price_markers)
    ):
        return AssistantIntent.SHOP

    if any(
        token in text
        for token in (
            "commande",
            "commandes",
            "slash",
            "que peux tu faire",
            "que peux-tu faire",
        )
    ):
        return AssistantIntent.COMMANDS

    if any(
        token in text
        for token in (
            "radio",
            "musique",
            "youtube",
            "chanson",
            "morceau",
        )
    ):
        return AssistantIntent.RADIO

    if any(token in text for token in ("double xp", "boost xp", "boost")):
        return AssistantIntent.BOOST

    if any(
        token in text
        for token in (
            "xp",
            "niveau",
            "rang",
            "level",
            "progression",
        )
    ):
        return AssistantIntent.XP

    if any(token in text for token in ("aide", "help", "bonjour", "salut")):
        return AssistantIntent.HELP

    return AssistantIntent.UNKNOWN


async def _read_xp_snapshot(user_id: int) -> dict:
    """Lit l'état XP sans utiliser ``get_user_data`` qui modifie last_accessed.

    Le cache mémoire est consulté sous le verrou du store. Si l'utilisateur n'y
    figure pas, le fichier persistant est lu sans hydrater ni modifier le cache.
    """

    uid = str(user_id)
    async with xp_store.lock:
        cached = xp_store.data.get(uid)
        if isinstance(cached, dict):
            return dict(cached)

    disk_data = await asyncio.to_thread(read_json_safe, xp_store.path)
    if isinstance(disk_data, dict):
        payload = disk_data.get(uid)
        if isinstance(payload, dict):
            return dict(payload)
    return {"xp": 0, "level": 0}


def _level_progress(xp: int, stored_level: int | None = None) -> tuple[int, int, int, int]:
    """Renvoie niveau, seuil courant, prochain seuil et XP restant."""

    xp = max(0, int(xp))
    calculated = int(math.isqrt(xp // 100))
    level = calculated
    if stored_level is not None and int(stored_level) == calculated:
        level = int(stored_level)
    current_threshold = level * level * 100
    next_threshold = (level + 1) * (level + 1) * 100
    remaining = max(0, next_threshold - xp)
    return level, current_threshold, next_threshold, remaining


def _active_personal_boost(user_id: int, *, now: datetime | None = None) -> tuple[bool, float]:
    """Lit le registre Double XP personnel sans le modifier."""

    from cogs import xp as xp_cog

    expiry = xp_cog.XP_BOOSTS.get(str(user_id))
    if expiry is None:
        return False, 0.0
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    else:
        expiry = expiry.astimezone(timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = (expiry - current).total_seconds()
    return seconds > 0, max(0.0, seconds)


def _format_remaining(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "moins d'une minute"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def _load_shop_snapshot() -> dict[str, dict]:
    """Lit le catalogue boutique sans créer ni réécrire de fichier."""

    try:
        raw = json.loads(SHOP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def build_response(intent: AssistantIntent) -> discord.Embed:
    """Construit les réponses statiques et les fallbacks de la V2."""

    if intent is AssistantIntent.COMMANDS:
        embed = discord.Embed(
            title="⌨️ Maître du jeu · Commandes",
            description=(
                "Tape **`/`** dans Discord pour afficher les commandes auxquelles "
                "tu as accès.\n\n"
                "Je peux aussi consulter en direct **ton XP, ton niveau, ton Double XP, "
                "le catalogue de la boutique et l'état de la radio**."
            ),
            colour=discord.Colour.teal(),
        )
    elif intent is AssistantIntent.UNKNOWN:
        embed = discord.Embed(
            title="🤖 Maître du jeu · V2",
            description=(
                "Je n'ai pas encore appris à répondre à cette question.\n\n"
                "Je peux aider sur **l'XP, les niveaux, le Double XP, la boutique, "
                "le Ticket Royal, la radio et les commandes**."
            ),
            colour=discord.Colour.orange(),
        )
    else:
        embed = discord.Embed(
            title="🤖 Maître du jeu",
            description=(
                "Je suis là pour t'aider à comprendre le Refuge.\n\n"
                "Tu peux maintenant me demander par exemple :\n"
                "• `combien j'ai d'XP ?`\n"
                "• `mon Double XP est actif ?`\n"
                "• `combien coûte le Ticket Royal ?`\n"
                "• `la radio fonctionne ?`\n"
                "• `pourquoi je n'ai pas gagné d'XP ?`"
            ),
            colour=discord.Colour.blurple(),
        )

    embed.set_footer(text="Maître du jeu · Assistant V2 en test")
    return embed


async def _build_xp_response(user_id: int) -> discord.Embed:
    data = await _read_xp_snapshot(user_id)
    xp = max(0, int(data.get("xp", 0)))
    stored_level = int(data.get("level", 0))
    level, current_threshold, next_threshold, remaining = _level_progress(
        xp, stored_level
    )
    span = max(1, next_threshold - current_threshold)
    progress = max(0, xp - current_threshold)
    percent = min(100.0, progress / span * 100.0)
    boost_active, boost_seconds = _active_personal_boost(user_id)
    boost_line = (
        f"⚡ **Double XP : actif** · {_format_remaining(boost_seconds)} restant"
        if boost_active
        else "⚪ **Double XP : inactif**"
    )

    embed = discord.Embed(
        title="🎮 Maître du jeu · Ton XP",
        description=(
            f"**XP total : {xp:,} XP**\n"
            f"**Niveau : {level}**\n"
            f"**Prochain niveau : {next_threshold:,} XP**\n"
            f"Il te manque **{remaining:,} XP**.\n"
            f"Progression du niveau : **{percent:.1f}%**\n\n"
            f"{boost_line}\n\n"
            "Rappel : les messages éligibles rapportent **8 XP** avec un cooldown "
            "d'une minute, et le vocal rapporte **3 XP par minute complète éligible**."
        ).replace(",", " "),
        colour=discord.Colour.blurple(),
    )
    embed.set_footer(text="Maître du jeu · Données XP en lecture seule")
    return embed


async def _build_boost_response(user_id: int) -> discord.Embed:
    active, seconds = _active_personal_boost(user_id)
    if active:
        status = (
            "⚡ Ton **Double XP personnel est actif**.\n"
            f"Temps restant estimé : **{_format_remaining(seconds)}**."
        )
        colour = discord.Colour.gold()
    else:
        status = (
            "⚪ Ton **Double XP personnel n'est pas actif** actuellement.\n"
            "Tu peux consulter la boutique pour voir les boosts disponibles."
        )
        colour = discord.Colour.light_grey()

    embed = discord.Embed(
        title="⚡ Maître du jeu · Double XP",
        description=(
            f"{status}\n\n"
            "Le bonus s'applique uniquement aux gains éligibles pendant sa période "
            "d'activation ; il ne modifie pas rétroactivement une ancienne session vocale."
        ),
        colour=colour,
    )
    embed.set_footer(text="Maître du jeu · État lu en temps réel")
    return embed


def _build_shop_response() -> discord.Embed:
    shop = _load_shop_snapshot()
    if not shop:
        description = (
            "Je ne peux pas lire le catalogue actif pour le moment. "
            "Aucun prix ne sera inventé : consulte le panneau Boutique."
        )
        colour = discord.Colour.orange()
    else:
        lines: list[str] = []
        for key, item in shop.items():
            name = str(item.get("name", key))
            if "vip" in key.casefold() or "vip" in name.casefold():
                continue
            price = item.get("price")
            price_text = f"{int(price)} XP" if isinstance(price, (int, float)) else "prix non défini"
            lines.append(f"• **{name}** — {price_text}")
        if lines:
            description = (
                "Voici les prix lus directement dans le **catalogue actif** :\n\n"
                + "\n".join(lines)
                + "\n\nLe panneau Boutique reste la référence pour les limites d'achat et l'activation."
            )
            colour = discord.Colour.gold()
        else:
            description = "Le catalogue actif ne contient actuellement aucun article public lisible."
            colour = discord.Colour.orange()

    embed = discord.Embed(
        title="🛒 Maître du jeu · Boutique",
        description=description,
        colour=colour,
    )
    embed.set_footer(text="Maître du jeu · Catalogue lu sans modification")
    return embed


def _radio_station_name(stream_url: str | None) -> str:
    stations = {
        RADIO_RAP_FR_STREAM_URL: "Rap FR",
        RADIO_RAP_STREAM_URL: "Rap US",
        ROCK_RADIO_STREAM_URL: "Rock",
        RADIO_STREAM_URL: "Hip-Hop",
    }
    if stream_url is None:
        return "suspendue temporairement"
    return stations.get(stream_url, "flux personnalisé")


def _build_radio_response(bot: commands.Bot) -> discord.Embed:
    radio = bot.get_cog("RadioCog")
    if radio is None:
        description = (
            "⚠️ Le module Radio n'est pas chargé actuellement. "
            "Je ne peux donc pas confirmer son état."
        )
        colour = discord.Colour.orange()
    else:
        stream_url = getattr(radio, "stream_url", None)
        station = _radio_station_name(stream_url)
        voice = getattr(radio, "voice", None)
        connected = bool(voice and getattr(voice, "is_connected", lambda: False)())
        playing = bool(voice and getattr(voice, "is_playing", lambda: False)())

        if stream_url is None:
            status = "⏸️ **Radio suspendue temporairement**"
            detail = "Une lecture musicale ponctuelle peut être en cours."
            colour = discord.Colour.purple()
        elif connected and playing:
            status = "🟢 **Radio active**"
            detail = "Le bot est connecté et le flux est en lecture."
            colour = discord.Colour.green()
        elif connected:
            status = "🟠 **Radio connectée mais flux non lu**"
            detail = "Le système peut être en phase de reprise ou de reconnexion."
            colour = discord.Colour.orange()
        else:
            status = "🔴 **Radio déconnectée**"
            detail = "Le flux est sélectionné mais aucune connexion vocale active n'est détectée."
            colour = discord.Colour.red()

        description = (
            f"{status}\n"
            f"Station sélectionnée : **{station}**\n\n"
            f"{detail}"
        )

    embed = discord.Embed(
        title="🎵 Maître du jeu · État radio",
        description=description,
        colour=colour,
    )
    embed.set_footer(text="Maître du jeu · État radio en lecture seule")
    return embed


async def _build_diagnostic_response(user_id: int) -> discord.Embed:
    data = await _read_xp_snapshot(user_id)
    xp = max(0, int(data.get("xp", 0)))
    level, _, next_threshold, remaining = _level_progress(
        xp, int(data.get("level", 0))
    )
    boost_active, boost_seconds = _active_personal_boost(user_id)
    boost = (
        f"actif ({_format_remaining(boost_seconds)} restant)"
        if boost_active
        else "inactif"
    )

    embed = discord.Embed(
        title="🔎 Maître du jeu · Diagnostic XP",
        description=(
            "Je peux vérifier ton **état actuel**, mais cette V2 ne conserve pas encore "
            "la cause exacte de chaque gain refusé.\n\n"
            f"• XP actuel : **{xp:,} XP**\n"
            f"• Niveau : **{level}**\n"
            f"• XP manquant : **{remaining:,} XP** vers {next_threshold:,}\n"
            f"• Double XP personnel : **{boost}**\n\n"
            "Causes normales les plus fréquentes : **cooldown message d'une minute**, "
            "moins d'une minute complète en vocal, ou activité non éligible."
        ).replace(",", " "),
        colour=discord.Colour.teal(),
    )
    embed.set_footer(text="Maître du jeu · Diagnostic V2 sans modification")
    return embed


async def build_live_response(
    intent: AssistantIntent,
    *,
    bot: commands.Bot,
    user_id: int,
) -> discord.Embed:
    """Construit une réponse V2, contextuelle lorsque l'intention le permet."""

    if intent is AssistantIntent.XP:
        return await _build_xp_response(user_id)
    if intent is AssistantIntent.BOOST:
        return await _build_boost_response(user_id)
    if intent is AssistantIntent.SHOP:
        return _build_shop_response()
    if intent is AssistantIntent.RADIO:
        return _build_radio_response(bot)
    if intent is AssistantIntent.DIAGNOSTIC:
        return await _build_diagnostic_response(user_id)
    return build_response(intent)


class MaitreDuJeuCog(commands.Cog):
    """Répond aux messages qui mentionnent directement le compte du bot."""

    COOLDOWN_SECONDS = 3.0

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_reply_at: dict[int, float] = {}

    def _is_on_cooldown(self, user_id: int) -> bool:
        now = time.monotonic()
        previous = self._last_reply_at.get(user_id)
        if previous is not None and now - previous < self.COOLDOWN_SECONDS:
            return True
        self._last_reply_at[user_id] = now
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Répond seulement aux mentions directes du bot sur un serveur."""

        if message.author.bot or message.guild is None:
            return

        bot_user = self.bot.user
        if bot_user is None:
            return

        mention_pattern = rf"<@!?{bot_user.id}>"
        if re.search(mention_pattern, message.content) is None:
            return

        if self._is_on_cooldown(message.author.id):
            return

        question = extract_question(message.content, bot_user.id)
        intent = classify_question(question)
        embed = await build_live_response(
            intent,
            bot=self.bot,
            user_id=message.author.id,
        )
        await message.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    """Charge le Maître du jeu via l'auto-discovery des cogs."""

    await bot.add_cog(MaitreDuJeuCog(bot))
