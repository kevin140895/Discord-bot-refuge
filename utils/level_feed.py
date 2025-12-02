from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from collections import Counter
from typing import Dict, Tuple

import discord

from config import LEVEL_FEED_CHANNEL_ID, ENABLE_GAME_LEVEL_FEED
from utils.messages import LEVEL_FEED_TEMPLATES
from utils.discord_utils import safe_message_edit

logger = logging.getLogger("level_feed")


GAME_SOURCES = {"pari_xp", "machine_a_sous"}
TEMPLATE_SOURCES = {key.rsplit("_", 1)[0] for key in LEVEL_FEED_TEMPLATES}
SUPPORTED_SOURCES = GAME_SOURCES | TEMPLATE_SOURCES
REFUGE_GAMER_COLOR = discord.Color(0xFF5DA2)


@dataclass
class LevelChange:
    user_id: int
    guild_id: int
    old_level: int
    new_level: int
    old_xp: int
    new_xp: int
    source: str


class LevelFeedRouter:
    def __init__(self) -> None:
        self.bot: discord.Client | None = None
        self._pending: Dict[Tuple[int, str], LevelChange] = {}
        self._tasks: Dict[Tuple[int, str], asyncio.Task] = {}
        self.metrics: Counter[str] = Counter()
        self._pari_xp_messages: Dict[Tuple[int, str], discord.Message] = {}

    def setup(self, bot: discord.Client) -> None:
        self.bot = bot

    def emit(self, event: LevelChange) -> None:
        key = (event.user_id, event.source)
        self._pending[key] = event
        if key in self._tasks:
            self.metrics["level_feed.coalesced"] += 1
            return
        self._tasks[key] = asyncio.create_task(self._dispatch_later(key))

    async def _dispatch_later(self, key: Tuple[int, str]) -> None:
        await asyncio.sleep(1)
        event = self._pending.pop(key, None)
        self._tasks.pop(key, None)
        if event:
            await self._handle(event)

    async def _handle(self, event: LevelChange) -> None:
        if event.source not in SUPPORTED_SOURCES:
            return
        if event.source in GAME_SOURCES and not ENABLE_GAME_LEVEL_FEED:
            return
        if not self.bot:
            return
        channel = self.bot.get_channel(LEVEL_FEED_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(LEVEL_FEED_CHANNEL_ID)
            except Exception:
                channel = None
        if not isinstance(channel, discord.abc.Messageable):
            self.metrics["level_feed.skipped_no_channel"] += 1
            logger.warning("level feed channel unavailable or invalid")
            return
        user = self.bot.get_user(event.user_id)
        mention = user.mention if user else f"<@{event.user_id}>"
        avatar_url = None
        if user is not None:
            avatar_url = getattr(getattr(user, "display_avatar", None), "url", None)
        xp_delta = event.new_xp - event.old_xp
        direction = "up" if event.new_level > event.old_level else "down"

        def _build_embed(title: str, description: str) -> discord.Embed:
            embed = discord.Embed(
                title=title,
                description=description,
                color=REFUGE_GAMER_COLOR,
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            embed.timestamp = discord.utils.utcnow()
            return embed

        if event.source == "pari_xp":
            if direction == "up":
                description = LEVEL_FEED_TEMPLATES["pari_xp_up"].format(
                    mention=mention,
                    new_level=event.new_level,
                    xp_gain=int(abs(xp_delta)),
                )
                embed = _build_embed("⬆️ LEVEL UP DANS LE REFUGE ! 🎮", description)
            else:
                description = LEVEL_FEED_TEMPLATES["pari_xp_down"].format(
                    mention=mention,
                    new_level=event.new_level,
                    xp_loss=int(abs(xp_delta)),
                )
                embed = _build_embed("⬇️ LEVEL DOWN", description)
            key = (event.user_id, direction)
            last_msg = self._pari_xp_messages.get(key)
            try:
                if last_msg:
                    await safe_message_edit(last_msg, embed=embed)
                else:
                    last_msg = await channel.send(embed=embed)
                    self._pari_xp_messages[key] = last_msg
                self.metrics[f"level_feed.sent.{event.source}"] += 1
            except discord.Forbidden:
                self.metrics["level_feed.skipped_no_channel"] += 1
                logger.warning("missing permission to send level feed message")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("failed to send level feed message: %s", exc)
            return

        template_key = f"{event.source}_{direction}"
        template = LEVEL_FEED_TEMPLATES.get(template_key)
        if not template:
            logger.warning("missing level feed template for %s", template_key)
            return
        description = template.format(
            mention=mention,
            new_level=event.new_level,
            xp_gain=int(abs(xp_delta)),
            xp_loss=int(abs(xp_delta)),
        )
        title = (
            "⬆️ LEVEL UP DANS LE REFUGE ! 🎮" if direction == "up" else "⬇️ LEVEL DOWN"
        )
        embed = _build_embed(title, description)
        try:
            await channel.send(embed=embed)
            self.metrics[f"level_feed.sent.{event.source}"] += 1
        except discord.Forbidden:
            self.metrics["level_feed.skipped_no_channel"] += 1
            logger.warning("missing permission to send level feed message")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("failed to send level feed message: %s", exc)


router = LevelFeedRouter()


def setup(bot: discord.Client) -> None:
    router.setup(bot)


def emit(event: LevelChange) -> None:
    router.emit(event)


__all__ = ["LevelChange", "router", "setup", "emit"]
