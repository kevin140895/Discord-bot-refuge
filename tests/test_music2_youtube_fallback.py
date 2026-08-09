from __future__ import annotations

from types import SimpleNamespace

import pytest

import cogs.music2_youtube_fallback as fallback


class DummyBot:
    def __init__(self, music=None) -> None:
        self.music = music

    def get_cog(self, name: str):
        return self.music if name == "Music2Cog" else None


def test_is_youtube_target_accepts_search_and_youtube_urls() -> None:
    assert fallback._is_youtube_target("daft punk one more time") is True
    assert fallback._is_youtube_target("https://www.youtube.com/watch?v=abc") is True
    assert fallback._is_youtube_target("https://youtu.be/abc") is True
    assert fallback._is_youtube_target("https://example.com/audio.mp3") is False


def test_fallback_uses_resilient_youtube_player_clients(monkeypatch) -> None:
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
            captured["download"] = download
            return {
                "entries": [
                    {
                        "title": "Found",
                        "webpage_url": "https://www.youtube.com/watch?v=abc",
                        "url": "https://cdn.example.test/audio",
                    }
                ]
            }

    monkeypatch.setattr(fallback.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    info = fallback._extract_with_youtube_fallback("daft punk one more time")

    assert captured["target"] == "ytsearch1:daft punk one more time"
    assert captured["download"] is False
    assert captured["options"]["format"] == "bestaudio/best"
    assert captured["options"]["extractor_args"]["youtube"]["player_client"] == [
        "android_vr",
        "web_embedded",
    ]
    assert info["title"] == "Found"


def test_wrapper_keeps_standard_extractor_when_it_succeeds(monkeypatch) -> None:
    calls = []

    def original(target: str):
        calls.append(target)
        return {"title": "Original"}

    music = SimpleNamespace(_extract_info_sync=original)
    cog = fallback.Music2YoutubeFallbackCog(DummyBot(music))
    monkeypatch.setattr(
        fallback,
        "_extract_with_youtube_fallback",
        lambda target: pytest.fail("fallback must not run"),
    )

    assert cog._install_if_possible() is True
    result = music._extract_info_sync("test song")

    assert result == {"title": "Original"}
    assert calls == ["test song"]


def test_wrapper_retries_youtube_after_standard_failure(monkeypatch) -> None:
    def original(_target: str):
        raise RuntimeError("standard failed")

    music = SimpleNamespace(_extract_info_sync=original)
    cog = fallback.Music2YoutubeFallbackCog(DummyBot(music))
    fallback_calls = []

    def fallback_extract(target: str):
        fallback_calls.append(target)
        return {"title": "Recovered"}

    monkeypatch.setattr(fallback, "_extract_with_youtube_fallback", fallback_extract)

    assert cog._install_if_possible() is True
    result = music._extract_info_sync("https://youtube.com/watch?v=abc")

    assert result == {"title": "Recovered"}
    assert fallback_calls == ["https://youtube.com/watch?v=abc"]


def test_wrapper_does_not_retry_non_youtube_url(monkeypatch) -> None:
    def original(_target: str):
        raise RuntimeError("standard failed")

    music = SimpleNamespace(_extract_info_sync=original)
    cog = fallback.Music2YoutubeFallbackCog(DummyBot(music))
    monkeypatch.setattr(
        fallback,
        "_extract_with_youtube_fallback",
        lambda target: pytest.fail("fallback must not run"),
    )

    assert cog._install_if_possible() is True
    with pytest.raises(RuntimeError, match="standard failed"):
        music._extract_info_sync("https://example.com/audio.mp3")


def test_unload_restores_original_extractor() -> None:
    def original(target: str):
        return {"title": target}

    music = SimpleNamespace(_extract_info_sync=original)
    cog = fallback.Music2YoutubeFallbackCog(DummyBot(music))

    assert cog._install_if_possible() is True
    assert music._extract_info_sync is not original

    cog.cog_unload()

    assert music._extract_info_sync is original
