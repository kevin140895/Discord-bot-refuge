"""Assistant V1 déclenché par une mention directe du bot.

Cette première version reste volontairement déterministe : elle répond à un
petit socle de questions sur le Refuge sans appeler de service IA et sans
modifier l'état du serveur. Les systèmes métiers existants restent les seules
sources capables d'effectuer des actions.
"""

from __future__ import annotations

import re
import time
import unicodedata
from enum import Enum

import discord
from discord.ext import commands


class AssistantIntent(str, Enum):
    """Intentions prises en charge par la V1 du Maître du jeu."""

    HELP = "help"
    XP = "xp"
    SHOP = "shop"
    RADIO = "radio"
    COMMANDS = "commands"
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
    """Classe une question dans le périmètre déterministe de la V1."""

    text = _normalize_text(question)
    if not text:
        return AssistantIntent.HELP

    # Les mots d'achat passent avant Double XP : « où acheter un double XP ? »
    # doit être traité comme une question boutique et non comme une question XP.
    if any(
        token in text
        for token in (
            "boutique",
            "acheter",
            "achat",
            "ticket royal",
            "ticket",
        )
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

    if any(
        token in text
        for token in (
            "xp",
            "niveau",
            "rang",
            "level",
        )
    ):
        return AssistantIntent.XP

    if any(token in text for token in ("aide", "help", "bonjour", "salut")):
        return AssistantIntent.HELP

    return AssistantIntent.UNKNOWN


def build_response(intent: AssistantIntent) -> discord.Embed:
    """Construit la réponse Discord correspondant à une intention V1."""

    if intent is AssistantIntent.XP:
        embed = discord.Embed(
            title="🎮 Maître du jeu · XP",
            description=(
                "Dans le Refuge, l'XP récompense ton activité éligible.\n\n"
                "• **Messages** : 8 XP, avec au maximum une attribution par minute "
                "et par membre.\n"
                "• **Vocal** : 3 XP par minute complète éligible.\n"
                "• **Double XP personnel** : il multiplie les gains concernés "
                "uniquement pendant sa période active.\n\n"
                "Utilise **`/rang`** pour consulter ton niveau et ta progression."
            ),
            colour=discord.Colour.blurple(),
        )
    elif intent is AssistantIntent.SHOP:
        embed = discord.Embed(
            title="🛒 Maître du jeu · Boutique",
            description=(
                "La boutique permet de dépenser tes XP contre les avantages "
                "disponibles dans le Refuge.\n\n"
                "• **Ticket Royal** : une tentative supplémentaire à la Machine à sous.\n"
                "• **Double XP 1h** : augmente tes gains personnels pendant sa durée.\n\n"
                "Le **panneau Boutique** affiche toujours les prix et limites "
                "actuellement appliqués."
            ),
            colour=discord.Colour.gold(),
        )
    elif intent is AssistantIntent.RADIO:
        embed = discord.Embed(
            title="🎵 Maître du jeu · Radio",
            description=(
                "La radio se pilote depuis son panneau dédié. Elle peut gérer le "
                "flux radio et les lectures musicales prévues par le bot.\n\n"
                "Après une lecture ponctuelle, le comportement attendu est de "
                "**revenir automatiquement à la radio en cours**. Si elle reste "
                "bloquée, mentionne-moi en décrivant ce que tu viens de lancer."
            ),
            colour=discord.Colour.purple(),
        )
    elif intent is AssistantIntent.COMMANDS:
        embed = discord.Embed(
            title="⌨️ Maître du jeu · Commandes",
            description=(
                "Tape **`/`** dans Discord pour afficher les commandes auxquelles "
                "tu as accès.\n\n"
                "Je peux déjà t'expliquer les fonctions principales du Refuge : "
                "**XP et niveaux, boutique, Ticket Royal, Double XP et radio**.\n"
                "Par exemple : `@Maître du jeu comment fonctionne le Double XP ?`"
            ),
            colour=discord.Colour.teal(),
        )
    elif intent is AssistantIntent.UNKNOWN:
        embed = discord.Embed(
            title="🤖 Maître du jeu · V1 test",
            description=(
                "Je n'ai pas encore appris à répondre à cette question.\n\n"
                "Pour cette V1, je peux aider sur **l'XP, les niveaux, la boutique, "
                "le Ticket Royal, le Double XP, la radio et les commandes**."
            ),
            colour=discord.Colour.orange(),
        )
    else:
        embed = discord.Embed(
            title="🤖 Maître du jeu",
            description=(
                "Je suis là pour t'aider à comprendre le Refuge.\n\n"
                "Mentionne-moi avec une question sur **l'XP, les niveaux, la "
                "boutique, le Ticket Royal, le Double XP, la radio ou les commandes**.\n\n"
                "Exemple : `@Maître du jeu comment gagner de l'XP ?`"
            ),
            colour=discord.Colour.blurple(),
        )

    embed.set_footer(text="Maître du jeu · Assistant V1 en test")
    return embed


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
        embed = build_response(intent)
        await message.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    """Charge le Maître du jeu via l'auto-discovery des cogs."""

    await bot.add_cog(MaitreDuJeuCog(bot))
