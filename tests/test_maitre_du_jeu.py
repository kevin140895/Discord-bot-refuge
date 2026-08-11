import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.maitre_du_jeu import (
    AssistantIntent,
    MaitreDuJeuCog,
    build_response,
    classify_question,
    extract_question,
)


def test_extract_question_removes_both_discord_mention_forms():
    assert extract_question("<@42> comment gagner de l'XP ?", 42) == "comment gagner de l'XP ?"
    assert extract_question("<@!42>, aide-moi", 42) == "aide-moi"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("comment gagner de l'XP ?", AssistantIntent.XP),
        ("à quoi sert le niveau ?", AssistantIntent.XP),
        ("où acheter un Ticket Royal ?", AssistantIntent.SHOP),
        ("où acheter un double xp ?", AssistantIntent.SHOP),
        ("comment fonctionne la radio ?", AssistantIntent.RADIO),
        ("quelles commandes puis-je utiliser ?", AssistantIntent.COMMANDS),
        ("salut", AssistantIntent.HELP),
        ("quelle est la capitale de la France ?", AssistantIntent.UNKNOWN),
    ],
)
def test_classify_question(question, expected):
    assert classify_question(question) is expected


def test_build_response_marks_unknown_questions_as_v1_test():
    embed = build_response(AssistantIntent.UNKNOWN)
    assert "V1 test" in embed.title
    assert "XP" in embed.description
    assert embed.footer.text == "Maître du jeu · Assistant V1 en test"


@pytest.mark.asyncio
async def test_on_message_replies_to_direct_bot_mention():
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)
    message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        guild=SimpleNamespace(id=123),
        content="<@42> comment gagner de l'XP ?",
        reply=AsyncMock(),
    )

    await cog.on_message(message)

    message.reply.assert_awaited_once()
    kwargs = message.reply.await_args.kwargs
    assert kwargs["mention_author"] is False
    assert kwargs["embed"].title == "🎮 Maître du jeu · XP"


@pytest.mark.asyncio
async def test_on_message_ignores_messages_without_bot_mention():
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)
    message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        guild=SimpleNamespace(id=123),
        content="comment gagner de l'XP ?",
        reply=AsyncMock(),
    )

    await cog.on_message(message)

    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_ignores_other_bots_and_direct_messages():
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)

    bot_message = SimpleNamespace(
        author=SimpleNamespace(id=8, bot=True),
        guild=SimpleNamespace(id=123),
        content="<@42> aide",
        reply=AsyncMock(),
    )
    dm_message = SimpleNamespace(
        author=SimpleNamespace(id=9, bot=False),
        guild=None,
        content="<@42> aide",
        reply=AsyncMock(),
    )

    await cog.on_message(bot_message)
    await cog.on_message(dm_message)

    bot_message.reply.assert_not_awaited()
    dm_message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_applies_short_per_user_cooldown():
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)
    message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        guild=SimpleNamespace(id=123),
        content="<@42> aide",
        reply=AsyncMock(),
    )

    await cog.on_message(message)
    await cog.on_message(message)

    message.reply.assert_awaited_once()
