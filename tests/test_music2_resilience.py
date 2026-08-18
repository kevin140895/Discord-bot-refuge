from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.music2 as music2
import utils.ytdlp_auth as ytdlp_auth


class DummyBot:
    def __init__(self, radio=None) -> None:
        self.radio = radio
        self.loop = None

    def get_cog(self, name: str):
        return self.radio if name == "RadioCog" else None

    def get_channel(self, _channel_id: int):
        return None


class DummyVoice:
    def __init__(self) -> None:
        self.playing = False
        self.paused = False

    def is_playing(self) -> bool:
        return self.playing

    def is_paused(self) -> bool:
        return self.paused

    def stop(self) -> None:
        self.playing = False
        self.paused = False


def test_ytdlp_options_bound_network_and_extractor_retries(monkeypatch):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, target, download=False):
            captured["target"] = target
            return {
                "title": "Found",
                "webpage_url": "https://example.test/found",
                "url": "https://cdn.example.test/audio",
            }

    monkeypatch.setattr(music2.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    cog = music2.Music2Cog(DummyBot())

    result = cog._extract_info_sync("https://example.test/found")

    assert result["title"] == "Found"
    assert captured["options"]["socket_timeout"] == music2.MUSIC2_YTDLP_SOCKET_TIMEOUT_SECONDS
    assert captured["options"]["retries"] == music2.MUSIC2_YTDLP_RETRIES
    assert captured["options"]["extractor_retries"] == music2.MUSIC2_YTDLP_EXTRACTOR_RETRIES


@pytest.mark.asyncio
async def test_extract_info_retries_once_after_transient_failure(monkeypatch):
    cog = music2.Music2Cog(DummyBot())
    attempts = []

    def flaky_extract(target: str):
        attempts.append(target)
        if len(attempts) == 1:
            raise RuntimeError("temporary YouTube failure")
        return {"title": "Recovered"}

    monkeypatch.setattr(cog, "_extract_info_sync", flaky_extract)
    monkeypatch.setattr(music2, "MUSIC2_EXTRACTION_RETRY_DELAY_SECONDS", 0)

    result = await cog._extract_info("query", purpose="test")

    assert result["title"] == "Recovered"
    assert attempts == ["query", "query"]


@pytest.mark.asyncio
async def test_url_lookup_uses_metadata_cache_but_stream_resolution_stays_fresh(monkeypatch):
    ytdlp_auth.clear_ytdlp_metadata_cache()
    cog = music2.Music2Cog(DummyBot())
    calls = []
    target = "https://www.youtube.com/watch?v=cache-test"

    def fake_extract(value: str):
        calls.append(value)
        return {
            "id": "cache-test",
            "title": "Cache Test",
            "webpage_url": target,
            "duration": 42,
            "uploader": "Tester",
            "url": f"https://signed-media.invalid/{len(calls)}",
            "http_headers": {"User-Agent": "temporary"},
        }

    monkeypatch.setattr(cog, "_extract_info_sync", fake_extract)

    first = await cog._extract_info(target, purpose="recherche URL")
    second = await cog._extract_info(target, purpose="recherche URL")
    fresh_stream = await cog._extract_info(target, purpose="résolution flux")

    assert first["url"] == "https://signed-media.invalid/1"
    assert "url" not in second
    assert "http_headers" not in second
    assert fresh_stream["url"] == "https://signed-media.invalid/2"
    assert calls == [target, target]


@pytest.mark.asyncio
async def test_text_search_uses_one_hour_metadata_cache(monkeypatch):
    ytdlp_auth.clear_ytdlp_metadata_cache()
    cog = music2.Music2Cog(DummyBot())
    calls = []
    target = "Daft Punk One More Time"
    webpage_url = "https://www.youtube.com/watch?v=search-cache"

    def fake_search(value: str):
        calls.append(value)
        return {
            "id": "search-cache",
            "title": "One More Time",
            "webpage_url": webpage_url,
            "duration": 320,
            "uploader": "Daft Punk",
            "url": "https://signed-media.invalid/search",
        }

    monkeypatch.setattr(cog, "_search_info_sync", fake_search)

    first = await cog._search_info(target)
    second = await cog._search_info("  daft   punk one more time  ")

    assert first["url"] == "https://signed-media.invalid/search"
    assert second["title"] == "One More Time"
    assert second["webpage_url"] == webpage_url
    assert "url" not in second
    assert calls == [target]


@pytest.mark.asyncio
async def test_play_next_forces_vod_profile_even_without_headers(monkeypatch):
    voice = DummyVoice()
    radio = SimpleNamespace(voice=voice)
    cog = music2.Music2Cog(DummyBot(radio))
    cog.refresh_panel = AsyncMock()
    cog._suspend_radio = AsyncMock(return_value=radio)
    cog._resolve_stream = AsyncMock(
        return_value=("https://cdn.example.test/audio", None)
    )
    cog.queue.append(
        music2.MusicTrack(
            title="Test Song",
            webpage_url="https://example.test/watch/123",
            requester_id=42,
        )
    )
    captured = {}

    def fake_play_stream(
        target_voice,
        stream_url,
        *,
        after=None,
        headers=None,
        on_demand=False,
    ):
        captured.update(
            voice=target_voice,
            stream_url=stream_url,
            headers=headers,
            on_demand=on_demand,
        )
        target_voice.playing = True

    monkeypatch.setattr(music2, "play_stream", fake_play_stream)

    await cog._play_next()

    assert captured["stream_url"] == "https://cdn.example.test/audio"
    assert captured["headers"] is None
    assert captured["on_demand"] is True
    assert cog.current is not None
    assert cog.current.title == "Test Song"


@pytest.mark.asyncio
async def test_restore_radio_keeps_target_when_reconnect_fails():
    radio = SimpleNamespace(
        stream_url=None,
        _connect_and_play=AsyncMock(side_effect=RuntimeError("voice unavailable")),
        _rename_for_stream=AsyncMock(),
    )
    cog = music2.Music2Cog(DummyBot(radio))
    cog._radio_restore_stream = music2.ROCK_RADIO_STREAM_URL

    await cog._restore_radio()

    assert radio.stream_url == music2.ROCK_RADIO_STREAM_URL
    assert cog._radio_restore_stream == music2.ROCK_RADIO_STREAM_URL
    radio._rename_for_stream.assert_not_awaited()
