import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import cogs.maitre_du_jeu as mdj
from cogs.maitre_du_jeu import AssistantIntent, classify_question


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Quand est ce qu'on ta créé ?", AssistantIntent.BOT_CREATED),
        ("Tu existes depuis quand ?", AssistantIntent.BOT_CREATED),
        ("Combien de membres sommes nous ?", AssistantIntent.MEMBER_COUNT),
        ("On est combien sur le serveur ?", AssistantIntent.MEMBER_COUNT),
        ("Comment on joue au refuge vivant ?", AssistantIntent.REFUGE_GUIDE),
        ("Comment fonctionne le Refuge ?", AssistantIntent.REFUGE_GUIDE),
        ("A quelle heure ferme/ouvre le casino ?", AssistantIntent.CASINO_HOURS),
        ("Quand ouvre le casino ?", AssistantIntent.CASINO_HOURS),
    ],
)
def test_faq_questions_are_deterministic(question, expected):
    assert classify_question(question) is expected


def test_bot_created_response_uses_discord_account_timestamp():
    created_at = datetime(2025, 8, 14, 12, 30, tzinfo=timezone.utc)
    bot = SimpleNamespace(user=SimpleNamespace(created_at=created_at))

    embed = mdj._build_bot_created_response(bot)

    timestamp = int(created_at.timestamp())
    assert "Ma création" in embed.title
    assert f"<t:{timestamp}:D>" in embed.description
    assert f"<t:{timestamp}:R>" in embed.description
    assert "Date fournie par Discord" in embed.footer.text


def test_member_count_response_reads_current_guild_count():
    guild = SimpleNamespace(name="Le Refuge", member_count=321)
    bot = SimpleNamespace(guilds=[guild])

    embed = mdj._build_member_count_response(bot)

    assert "321 membres" in embed.description
    assert "Le Refuge" in embed.description
    assert "lu sur Discord" in embed.footer.text


def test_refuge_guide_describes_real_living_world_inputs():
    embed = mdj._build_refuge_guide_response()

    assert "Refuge vivant" in embed.title
    assert "vocal" in embed.description
    assert "Feu" in embed.description
    assert "succès" in embed.description
    assert "Hall" in embed.description
    assert "paris et jackpots" in embed.description
    assert "Casino" in embed.description
    assert "Chantier" in embed.description
    assert "Explorer" in embed.description
    assert "Mon empreinte" in embed.description
    assert "Chronologie" in embed.description


def test_casino_hours_response_uses_config_and_live_state(monkeypatch):
    monkeypatch.setattr(mdj, "CASINO_OPEN_HOUR", 10)
    monkeypatch.setattr(mdj, "CASINO_CLOSE_HOUR", 6)
    monkeypatch.setattr(mdj, "casino_is_open", lambda: True)

    embed = mdj._build_casino_hours_response()

    assert "10h00" in embed.description
    assert "06h00" in embed.description
    assert "le lendemain" in embed.description
    assert "actuellement ouvert" in embed.description
    assert "configuration" in embed.footer.text


@pytest.mark.asyncio
async def test_live_response_routes_member_count_without_ai():
    guild = SimpleNamespace(name="Le Refuge", member_count=999)
    bot = SimpleNamespace(guilds=[guild])

    embed = await mdj.build_live_response(
        AssistantIntent.MEMBER_COUNT,
        bot=bot,
        user_id=7,
    )

    assert "999 membres" in embed.description
