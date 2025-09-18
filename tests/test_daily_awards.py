import json
from datetime import datetime as real_datetime
from types import SimpleNamespace

import pytest
from unittest import mock
from unittest.mock import AsyncMock

import discord

import cogs.daily_awards as daily_awards
from cogs.daily_awards import DailyAwards, today_str_eu_paris


@pytest.mark.asyncio
async def test_build_embed_partial():
    cog = DailyAwards.__new__(DailyAwards)
    data = {
        "top3": {"mvp": [{"id": 1, "score": 10, "messages": 5, "voice": 30}]}
    }

    embed = await DailyAwards._build_embed(cog, data)
    assert embed.title.startswith("📢 Annonce")
    assert embed.fields[0].name == "MVP"
    assert "Aucun gagnant" in embed.fields[1].value
    assert "Aucun gagnant" in embed.fields[2].value


@pytest.mark.asyncio
async def test_maybe_award_partial_publishes():
    channel = SimpleNamespace(send=AsyncMock(), fetch_message=AsyncMock())

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._read_state = lambda: {}
    cog._write_state = lambda state: None
    cog._build_embed = AsyncMock(return_value=discord.Embed())
    cog._get_announce_channel = AsyncMock(return_value=channel)

    data = {"date": "2024-01-01", "winners": {"mvp": 1, "msg": None, "vc": None}}

    await DailyAwards._maybe_award(cog, data)

    cog._build_embed.assert_awaited_once()
    args, kwargs = cog._build_embed.await_args
    payload = args[0]
    assert payload["winners"] == data["winners"]
    assert "top3" in payload
    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_award_edits_existing():
    today = today_str_eu_paris()
    msg = SimpleNamespace(embeds=[], edit=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(), fetch_message=AsyncMock(return_value=msg))

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._read_state = lambda: {"last_posted_date": today, "last_message_id": 123}
    cog._write_state = lambda state: None
    cog._build_embed = AsyncMock(return_value=discord.Embed())
    cog._get_announce_channel = AsyncMock(return_value=channel)

    await DailyAwards._maybe_award(cog, {"top3": {}})

    channel.fetch_message.assert_awaited_once_with(123)
    msg.edit.assert_awaited_once()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_award_reposts_when_missing():
    today = today_str_eu_paris()
    state = {"last_posted_date": today, "last_message_id": 123}
    channel = SimpleNamespace(
        send=AsyncMock(return_value=SimpleNamespace(id=456)),
        fetch_message=AsyncMock(
            side_effect=discord.NotFound(mock.Mock(status=404), "Not Found")
        ),
    )

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._read_state = lambda: state
    cog._write_state = lambda data: state.update(data)
    cog._build_embed = AsyncMock(return_value=discord.Embed())
    cog._get_announce_channel = AsyncMock(return_value=channel)

    await DailyAwards._maybe_award(cog, {"top3": {}})

    channel.send.assert_awaited_once()
    assert state["last_message_id"] == 456


def test_load_latest_award_data_prefers_winners(tmp_path, monkeypatch):
    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return real_datetime(2024, 1, 3)
            return real_datetime(2024, 1, 3, tzinfo=tz)

    winners_file = tmp_path / "daily_winners.json"
    winners_file.write_text(
        json.dumps(
            {
                "2024-01-01": {"top3": {"mvp": []}, "winners": {"mvp": 1, "msg": 2, "vc": 3}},
                "2024-01-02": {"top3": {"mvp": [{"id": 2}]}, "winners": {"mvp": 2}},
            }
        )
    )
    rank_file = tmp_path / "daily_ranking.json"
    rank_file.write_text(json.dumps({"date": "2024-01-01", "top3": {}, "winners": {}}))

    monkeypatch.setattr(daily_awards, "DAILY_WINNERS_FILE", str(winners_file))
    monkeypatch.setattr(daily_awards, "DAILY_RANK_FILE", str(rank_file))
    monkeypatch.setattr(daily_awards, "datetime", FixedDatetime)

    result = daily_awards._load_latest_award_data()

    assert result["date"] == "2024-01-02"
    assert result["winners"]["mvp"] == 2
    assert "top3" in result


def test_load_latest_award_data_ignores_stale_winners(tmp_path, monkeypatch):
    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return real_datetime(2024, 1, 3)
            return real_datetime(2024, 1, 3, tzinfo=tz)

    winners_file = tmp_path / "daily_winners.json"
    winners_file.write_text(
        json.dumps(
            {
                "2024-01-01": {"top3": {"mvp": [{"id": 1}]}, "winners": {"mvp": 1}},
            }
        )
    )
    rank_file = tmp_path / "daily_ranking.json"
    rank_file.write_text(
        json.dumps(
            {
                "date": "2024-01-02",
                "top3": {"mvp": [{"id": 9}]},
                "winners": {"mvp": 9, "msg": None, "vc": None},
            }
        )
    )

    monkeypatch.setattr(daily_awards, "DAILY_WINNERS_FILE", str(winners_file))
    monkeypatch.setattr(daily_awards, "DAILY_RANK_FILE", str(rank_file))
    monkeypatch.setattr(daily_awards, "datetime", FixedDatetime)

    result = daily_awards._load_latest_award_data()

    assert result["date"] == "2024-01-02"
    assert result["winners"]["mvp"] == 9
    assert result["top3"]["mvp"][0]["id"] == 9


def test_load_latest_award_data_fallback(tmp_path, monkeypatch):
    winners_file = tmp_path / "daily_winners.json"
    winners_file.write_text(json.dumps({}))
    rank_file = tmp_path / "daily_ranking.json"
    rank_file.write_text(
        json.dumps(
            {
                "date": "2024-01-03",
                "top3": {"mvp": [{"id": 5}]},
                "winners": {"mvp": 5, "msg": None, "vc": None},
            }
        )
    )

    monkeypatch.setattr(daily_awards, "DAILY_WINNERS_FILE", str(winners_file))
    monkeypatch.setattr(daily_awards, "DAILY_RANK_FILE", str(rank_file))

    result = daily_awards._load_latest_award_data()

    assert result["date"] == "2024-01-03"
    assert result["winners"]["mvp"] == 5
    assert result["top3"]["mvp"][0]["id"] == 5


