from __future__ import annotations

from types import SimpleNamespace

import cogs.music2 as music2


class DummyBot:
    def get_cog(self, _name: str):
        return None

    def get_channel(self, _channel_id: int):
        return None


def test_search_checks_up_to_five_results_and_skips_unavailable(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, target, download=False):
            calls.append((target, self.options))
            if target == "ytsearch5:booba audio":
                return {
                    "entries": [
                        {
                            "id": "bad-video",
                            "title": "Booba - Cozy Camping Night - Cartoon for kids",
                        },
                        {
                            "id": "good-video",
                            "title": "Booba - Valid Song",
                        },
                    ]
                }
            if target == "https://www.youtube.com/watch?v=bad-video":
                raise RuntimeError("This video is not available")
            if target == "https://www.youtube.com/watch?v=good-video":
                return {
                    "id": "good-video",
                    "title": "Booba - Valid Song",
                    "webpage_url": "https://www.youtube.com/watch?v=good-video",
                    "url": "https://cdn.example.test/good-audio",
                }
            raise AssertionError(f"unexpected target: {target}")

    monkeypatch.setattr(music2.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    cog = music2.Music2Cog(DummyBot())

    result = cog._search_info_sync("booba")

    assert result["title"] == "Booba - Valid Song"
    assert [target for target, _options in calls] == [
        "ytsearch5:booba audio",
        "https://www.youtube.com/watch?v=bad-video",
        "https://www.youtube.com/watch?v=good-video",
    ]
    search_options = calls[0][1]
    assert search_options["extract_flat"] == "in_playlist"
    assert search_options["ignoreerrors"] is True


def test_search_candidate_prefers_explicit_webpage_url():
    cog = music2.Music2Cog(DummyBot())

    assert cog._search_candidate_url(
        {
            "id": "video-id",
            "url": "video-id",
            "webpage_url": "https://www.youtube.com/watch?v=explicit",
        }
    ) == "https://www.youtube.com/watch?v=explicit"


def test_direct_url_extraction_never_turns_into_search(monkeypatch):
    captured = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, target, download=False):
            captured.append(target)
            return {
                "title": "Exact video",
                "webpage_url": target,
                "url": "https://cdn.example.test/exact-audio",
            }

    monkeypatch.setattr(music2.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    cog = music2.Music2Cog(DummyBot())
    exact_url = "https://www.youtube.com/watch?v=exact-id"

    result = cog._extract_info_sync(exact_url)

    assert result["webpage_url"] == exact_url
    assert captured == [exact_url]
