from unittest.mock import AsyncMock

import pytest

import cogs.music2 as music2


class DummyBot:
    def get_cog(self, _name: str):
        return None

    def get_channel(self, _channel_id: int):
        return None


def test_track_from_info_keeps_fresh_resolved_audio_stream(monkeypatch):
    monkeypatch.setattr(music2.time, "monotonic", lambda: 100.0)
    cog = music2.Music2Cog(DummyBot())

    track = cog._track_from_info(
        {
            "title": "Playable Song",
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "url": "https://cdn.example.test/audio?token=short-lived",
            "http_headers": {
                "User-Agent": "yt-dlp",
                "Referer": "https://www.youtube.com/",
            },
            "duration": 180,
        },
        requester_id=42,
    )

    assert track.webpage_url == "https://www.youtube.com/watch?v=abc123"
    assert track.cached_stream_url == "https://cdn.example.test/audio?token=short-lived"
    assert track.cached_stream_resolved_at == 100.0
    assert "User-Agent: yt-dlp" in track.cached_stream_headers
    assert "Referer: https://www.youtube.com/" in track.cached_stream_headers


@pytest.mark.asyncio
async def test_resolve_stream_reuses_fresh_cache_without_second_ytdlp_call(monkeypatch):
    monkeypatch.setattr(music2.time, "monotonic", lambda: 120.0)
    cog = music2.Music2Cog(DummyBot())
    cog._extract_info = AsyncMock(
        side_effect=AssertionError("yt-dlp must not run for a fresh cached stream")
    )
    track = music2.MusicTrack(
        title="Playable Song",
        webpage_url="https://www.youtube.com/watch?v=abc123",
        requester_id=42,
        cached_stream_url="https://cdn.example.test/audio",
        cached_stream_headers="User-Agent: yt-dlp\r\n",
        cached_stream_resolved_at=100.0,
    )

    stream_url, headers = await cog._resolve_stream(track)

    assert stream_url == "https://cdn.example.test/audio"
    assert headers == "User-Agent: yt-dlp\r\n"
    cog._extract_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_stream_refreshes_expired_cache(monkeypatch):
    times = iter([500.0, 501.0])
    monkeypatch.setattr(music2.time, "monotonic", lambda: next(times))
    cog = music2.Music2Cog(DummyBot())
    cog._extract_info = AsyncMock(
        return_value={
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "url": "https://cdn.example.test/fresh-audio",
            "http_headers": {"User-Agent": "fresh"},
        }
    )
    track = music2.MusicTrack(
        title="Queued Song",
        webpage_url="https://www.youtube.com/watch?v=abc123",
        requester_id=42,
        cached_stream_url="https://cdn.example.test/expired-audio",
        cached_stream_resolved_at=0.0,
    )

    stream_url, headers = await cog._resolve_stream(track)

    assert stream_url == "https://cdn.example.test/fresh-audio"
    assert headers == "User-Agent: fresh\r\n"
    assert track.cached_stream_url == stream_url
    assert track.cached_stream_headers == headers
    assert track.cached_stream_resolved_at == 501.0
    cog._extract_info.assert_awaited_once_with(
        track.webpage_url,
        purpose="résolution flux",
    )


def test_track_without_resolved_media_keeps_cache_empty():
    cog = music2.Music2Cog(DummyBot())

    track = cog._track_from_info(
        {
            "title": "Metadata Only",
            "webpage_url": "https://www.youtube.com/watch?v=metadata",
        },
        requester_id=7,
    )

    assert track.cached_stream_url is None
    assert track.cached_stream_headers is None
    assert track.cached_stream_resolved_at is None
