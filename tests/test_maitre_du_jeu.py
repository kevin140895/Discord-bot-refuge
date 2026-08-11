import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import cogs.maitre_du_jeu as mdj
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
        ("combien j'ai d'XP ?", AssistantIntent.XP),
        ("à quoi sert le niveau ?", AssistantIntent.XP),
        ("mon Double XP est actif ?", AssistantIntent.BOOST),
        ("combien de temps reste mon boost ?", AssistantIntent.BOOST),
        ("où acheter un Ticket Royal ?", AssistantIntent.SHOP),
        ("où acheter un double xp ?", AssistantIntent.SHOP),
        ("combien coûte le Double XP ?", AssistantIntent.SHOP),
        ("comment fonctionne la radio ?", AssistantIntent.RADIO),
        ("la radio fonctionne ?", AssistantIntent.RADIO),
        ("quelles commandes puis-je utiliser ?", AssistantIntent.COMMANDS),
        ("pourquoi je n'ai pas gagné d'XP ?", AssistantIntent.DIAGNOSTIC),
        ("pourquoi je n’ai pas reçu d’XP ?", AssistantIntent.DIAGNOSTIC),
        ("salut", AssistantIntent.HELP),
        ("quelle est la capitale de la France ?", AssistantIntent.UNKNOWN),
    ],
)
def test_classify_question(question, expected):
    assert classify_question(question) is expected


def test_build_response_marks_unknown_questions_as_v2():
    embed = build_response(AssistantIntent.UNKNOWN)
    assert "V2" in embed.title
    assert "XP" in embed.description
    assert embed.footer.text == "Maître du jeu · Assistant V2 en test"


@pytest.mark.asyncio
async def test_xp_response_reads_live_snapshot_without_mutation(monkeypatch):
    monkeypatch.setattr(
        mdj,
        "_read_xp_snapshot",
        AsyncMock(return_value={"xp": 850, "level": 2}),
    )
    monkeypatch.setattr(mdj, "_active_personal_boost", lambda user_id: (True, 1800.0))

    embed = await mdj._build_xp_response(7)

    assert embed.title == "🎮 Maître du jeu · Ton XP"
    assert "850 XP" in embed.description
    assert "Niveau : 2" in embed.description
    assert "50 XP" in embed.description
    assert "Double XP : actif" in embed.description
    assert "30 min" in embed.description


@pytest.mark.asyncio
async def test_boost_response_reports_inactive_state(monkeypatch):
    monkeypatch.setattr(mdj, "_active_personal_boost", lambda user_id: (False, 0.0))

    embed = await mdj._build_boost_response(7)

    assert "n'est pas actif" in embed.description
    assert "temps réel" in embed.footer.text


def test_shop_response_reads_current_catalogue(tmp_path, monkeypatch):
    shop_file = tmp_path / "shop.json"
    shop_file.write_text(
        json.dumps(
            {
                "ticket_royal": {"name": "Ticket Royal", "price": 500},
                "double_xp_1h": {"name": "Double XP 1h", "price": 300},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mdj, "SHOP_FILE", shop_file)

    embed = mdj._build_shop_response()

    assert "Ticket Royal" in embed.description
    assert "500 XP" in embed.description
    assert "Double XP 1h" in embed.description
    assert "300 XP" in embed.description


def test_shop_response_never_invents_price_when_catalogue_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mdj, "SHOP_FILE", tmp_path / "missing.json")

    embed = mdj._build_shop_response()

    assert "Aucun prix ne sera inventé" in embed.description


def test_radio_response_reports_live_station_and_playback():
    voice = SimpleNamespace(is_connected=lambda: True, is_playing=lambda: True)
    radio = SimpleNamespace(stream_url=mdj.RADIO_STREAM_URL, voice=voice)
    bot = SimpleNamespace(get_cog=lambda name: radio if name == "RadioCog" else None)

    embed = mdj._build_radio_response(bot)

    assert "Radio active" in embed.description
    assert "Hip-Hop" in embed.description
    assert "flux est en lecture" in embed.description


def test_radio_response_understands_music_suspension():
    voice = SimpleNamespace(is_connected=lambda: True, is_playing=lambda: True)
    radio = SimpleNamespace(stream_url=None, voice=voice)
    bot = SimpleNamespace(get_cog=lambda name: radio if name == "RadioCog" else None)

    embed = mdj._build_radio_response(bot)

    assert "suspendue temporairement" in embed.description
    assert "lecture musicale" in embed.description


@pytest.mark.asyncio
async def test_diagnostic_is_explicit_about_v2_limit(monkeypatch):
    monkeypatch.setattr(
        mdj,
        "_read_xp_snapshot",
        AsyncMock(return_value={"xp": 850, "level": 2}),
    )
    monkeypatch.setattr(mdj, "_active_personal_boost", lambda user_id: (False, 0.0))

    embed = await mdj._build_diagnostic_response(7)

    assert "état actuel" in embed.description
    assert "cause exacte" in embed.description
    assert "cooldown message" in embed.description
    assert "850 XP" in embed.description


@pytest.mark.asyncio
async def test_on_message_replies_to_direct_bot_mention(monkeypatch):
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)
    live_embed = discord.Embed(title="live")
    live_builder = AsyncMock(return_value=live_embed)
    monkeypatch.setattr(mdj, "build_live_response", live_builder)
    message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        guild=SimpleNamespace(id=123),
        content="<@42> combien j'ai d'XP ?",
        reply=AsyncMock(),
    )

    await cog.on_message(message)

    live_builder.assert_awaited_once_with(
        AssistantIntent.XP,
        bot=bot,
        user_id=7,
    )
    message.reply.assert_awaited_once()
    kwargs = message.reply.await_args.kwargs
    assert kwargs["mention_author"] is False
    assert kwargs["embed"] is live_embed


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
async def test_on_message_applies_short_per_user_cooldown(monkeypatch):
    bot = SimpleNamespace(user=SimpleNamespace(id=42))
    cog = MaitreDuJeuCog(bot)
    monkeypatch.setattr(
        mdj,
        "build_live_response",
        AsyncMock(return_value=discord.Embed(title="live")),
    )
    message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        guild=SimpleNamespace(id=123),
        content="<@42> aide",
        reply=AsyncMock(),
    )

    await cog.on_message(message)
    await cog.on_message(message)

    message.reply.assert_awaited_once()
