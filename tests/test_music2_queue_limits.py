from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.music2 as music2


class DummyBot:
    loop = None

    def get_cog(self, _name: str):
        return None

    def get_channel(self, _channel_id: int):
        return None


def _track(requester_id: int, index: int = 0) -> music2.MusicTrack:
    return music2.MusicTrack(
        title=f"Track {index}",
        webpage_url=f"https://example.test/{requester_id}/{index}",
        requester_id=requester_id,
    )


def _panel_text(cog: music2.Music2Cog) -> str:
    return "\n".join(
        item.content
        for item in cog.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_music2_panel_displays_queue_policy():
    cog = music2.Music2Cog(DummyBot())

    text = _panel_text(cog)

    assert f"{music2.MUSIC2_QUEUE_MAX_TRACKS} titres en attente" in text
    assert f"{music2.MUSIC2_MAX_TRACKS_PER_MEMBER} titres max par membre" in text
    assert f"{music2.MUSIC2_ADD_COOLDOWN_SECONDS:g} s entre deux ajouts" in text


@pytest.mark.asyncio
async def test_global_queue_reservations_are_atomic_and_bounded():
    cog = music2.Music2Cog(DummyBot())

    results = await asyncio.gather(
        *(cog._reserve_queue_slot(member_id) for member_id in range(1, 31))
    )

    accepted = [result for result in results if result is None]
    rejected = [result for result in results if result is not None]
    assert len(accepted) == music2.MUSIC2_QUEUE_MAX_TRACKS
    assert len(rejected) == 30 - music2.MUSIC2_QUEUE_MAX_TRACKS
    assert sum(cog._pending_adds.values()) == music2.MUSIC2_QUEUE_MAX_TRACKS
    assert all("file Music 2.0 est pleine" in result for result in rejected)


@pytest.mark.asyncio
async def test_member_limit_counts_current_queue_and_pending_reservation():
    cog = music2.Music2Cog(DummyBot())
    member_id = 42
    cog.current = _track(member_id, 0)
    for index in range(1, music2.MUSIC2_MAX_TRACKS_PER_MEMBER - 1):
        cog.queue.append(_track(member_id, index))

    first = await cog._reserve_queue_slot(member_id)
    second = await cog._reserve_queue_slot(member_id)

    assert first is None
    assert second is not None
    assert f"{music2.MUSIC2_MAX_TRACKS_PER_MEMBER} titres maximum" in second
    assert cog._active_tracks_for_member(member_id) == music2.MUSIC2_MAX_TRACKS_PER_MEMBER


@pytest.mark.asyncio
async def test_member_cooldown_survives_failed_or_released_reservation(monkeypatch):
    cog = music2.Music2Cog(DummyBot())
    clock = [100.0]
    monkeypatch.setattr(music2.time, "monotonic", lambda: clock[0])

    assert await cog._reserve_queue_slot(42) is None
    await cog._release_queue_reservation(42)

    clock[0] = 101.0
    rejection = await cog._reserve_queue_slot(42)
    assert rejection is not None
    assert "4.0 s" in rejection

    clock[0] = 105.1
    assert await cog._reserve_queue_slot(42) is None


@pytest.mark.asyncio
async def test_full_queue_rejects_before_defer_and_ytdlp(monkeypatch):
    class DummyMember:
        def __init__(self) -> None:
            self.id = 999
            self.voice = SimpleNamespace(
                channel=SimpleNamespace(id=music2.RADIO_VC_ID)
            )

    monkeypatch.setattr(music2.discord, "Member", DummyMember)
    cog = music2.Music2Cog(DummyBot())
    for index in range(music2.MUSIC2_QUEUE_MAX_TRACKS):
        cog.queue.append(_track(index + 1, index))
    cog._search_info = AsyncMock()

    response = SimpleNamespace(
        send_message=AsyncMock(),
        defer=AsyncMock(),
    )
    interaction = SimpleNamespace(
        user=DummyMember(),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.add_track_from_interaction(interaction, "test song")

    response.send_message.assert_awaited_once()
    response.defer.assert_not_awaited()
    cog._search_info.assert_not_awaited()
    assert "pleine" in response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_extraction_failure_releases_reserved_capacity(monkeypatch):
    class DummyMember:
        def __init__(self) -> None:
            self.id = 777
            self.voice = SimpleNamespace(
                channel=SimpleNamespace(id=music2.RADIO_VC_ID)
            )

    monkeypatch.setattr(music2.discord, "Member", DummyMember)
    cog = music2.Music2Cog(DummyBot())
    cog._search_info = AsyncMock(side_effect=RuntimeError("extractor down"))

    response = SimpleNamespace(
        send_message=AsyncMock(),
        defer=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        user=DummyMember(),
        response=response,
        followup=followup,
    )

    await cog.add_track_from_interaction(interaction, "test song")

    response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    followup.send.assert_awaited_once_with(
        "❌ Impossible de trouver ou lire ce titre.",
        ephemeral=True,
    )
    assert cog._pending_adds == {}
    assert 777 in cog._last_add_at
