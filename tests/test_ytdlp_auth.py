from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import utils.ytdlp_auth as ytdlp_auth


COOKIE_TEXT = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tTEST_SESSION\tsuper-secret-cookie\n"
)


def _encoded_cookie() -> str:
    return base64.b64encode(COOKIE_TEXT.encode("utf-8")).decode("ascii")


def test_base64_secret_is_materialised_as_private_netscape_file(tmp_path):
    config = ytdlp_auth.load_ytdlp_auth_config(
        {"YOUTUBE_COOKIES_B64": _encoded_cookie()},
        temp_dir=str(tmp_path),
    )

    assert config.source == "railway_secret"
    assert config.cookiefile is not None
    cookie_path = Path(config.cookiefile)
    assert cookie_path.parent == tmp_path
    assert cookie_path.read_text(encoding="utf-8") == COOKIE_TEXT
    assert os.stat(cookie_path).st_mode & 0o777 == 0o600


def test_explicit_cookie_file_has_priority_over_base64_secret(tmp_path):
    explicit = tmp_path / "cookies.txt"
    explicit.write_text(COOKIE_TEXT, encoding="utf-8")

    config = ytdlp_auth.load_ytdlp_auth_config(
        {
            "YOUTUBE_COOKIES_FILE": str(explicit),
            "YOUTUBE_COOKIES_B64": _encoded_cookie(),
        },
        temp_dir=str(tmp_path / "unused"),
    )

    assert config.cookiefile == str(explicit)
    assert config.source == "file"


def test_configure_injects_cookiefile_and_user_agent_without_overwriting_options(tmp_path):
    config = ytdlp_auth.configure_ytdlp_auth(
        {
            "YOUTUBE_COOKIES_B64": _encoded_cookie(),
            "YOUTUBE_USER_AGENT": "Browser UA/1.0",
        },
        temp_dir=str(tmp_path),
    )

    options = ytdlp_auth.augment_ytdlp_options(
        {
            "quiet": True,
            "http_headers": {"Accept-Language": "fr-FR"},
        }
    )

    assert options["cookiefile"] == config.cookiefile
    assert options["http_headers"] == {
        "Accept-Language": "fr-FR",
        "User-Agent": "Browser UA/1.0",
    }

    explicit = ytdlp_auth.augment_ytdlp_options(
        {
            "cookiefile": "/already/configured.txt",
            "http_headers": {"User-Agent": "Explicit UA"},
        }
    )
    assert explicit["cookiefile"] == "/already/configured.txt"
    assert explicit["http_headers"]["User-Agent"] == "Explicit UA"


def test_invalid_base64_is_safe_and_secret_is_never_logged(tmp_path, caplog):
    secret = "this-is-not-valid-base64!!!"
    caplog.set_level(logging.WARNING)

    config = ytdlp_auth.load_ytdlp_auth_config(
        {"YOUTUBE_COOKIES_B64": secret},
        temp_dir=str(tmp_path),
    )

    assert config.cookiefile is None
    assert "base64 invalide" in caplog.text
    assert secret not in caplog.text


def test_non_netscape_cookie_payload_is_rejected(tmp_path):
    invalid = base64.b64encode(b"name=value\n").decode("ascii")

    config = ytdlp_auth.load_ytdlp_auth_config(
        {"YOUTUBE_COOKIES_B64": invalid},
        temp_dir=str(tmp_path),
    )

    assert config.cookiefile is None


def test_no_cookie_secret_keeps_anonymous_mode_but_can_keep_user_agent(tmp_path):
    config = ytdlp_auth.configure_ytdlp_auth(
        {"YOUTUBE_USER_AGENT": "Browser UA/2.0"},
        temp_dir=str(tmp_path),
    )

    assert config.cookiefile is None
    options = ytdlp_auth.augment_ytdlp_options({"quiet": True})
    assert options["quiet"] is True
    assert options["http_headers"]["User-Agent"] == "Browser UA/2.0"


def _install_fake_extractor(monkeypatch):
    calls: list[tuple[str, tuple, dict]] = []

    def fake_init(self, params=None, auto_init=True):
        self.params = dict(params or {})

    def fake_extract_info(self, url, *args, **kwargs):
        calls.append((str(url), args, dict(kwargs)))
        return {
            "id": f"result-{len(calls)}",
            "title": f"Result {len(calls)}",
            "url": f"https://media.invalid/{len(calls)}",
            "webpage_url": str(url),
        }

    monkeypatch.setattr(ytdlp_auth._ORIGINAL_YOUTUBE_DL, "__init__", fake_init)
    monkeypatch.setattr(
        ytdlp_auth._ORIGINAL_YOUTUBE_DL,
        "extract_info",
        fake_extract_info,
    )
    ytdlp_auth.clear_ytdlp_cache()
    return calls


def test_search_extract_info_is_cached_for_one_hour(monkeypatch, caplog):
    calls = _install_fake_extractor(monkeypatch)
    clock = [1_000.0]
    monkeypatch.setattr(ytdlp_auth.time, "monotonic", lambda: clock[0])
    caplog.set_level(logging.INFO)

    ydl = ytdlp_auth.RefugeYoutubeDL(
        {"extract_flat": "in_playlist", "skip_download": True}
    )
    first = ydl.extract_info("ytsearch5:daft punk audio", download=False)
    second = ydl.extract_info("ytsearch5:daft punk audio", download=False)

    assert first == second
    assert len(calls) == 1
    assert "cache miss kind=search" in caplog.text
    assert "cache hit kind=search" in caplog.text

    clock[0] += ytdlp_auth.YTDLP_SEARCH_CACHE_TTL_SECONDS + 1
    third = ydl.extract_info("ytsearch5:daft punk audio", download=False)

    assert third["id"] == "result-2"
    assert len(calls) == 2


def test_direct_extract_info_uses_short_stream_safe_ttl(monkeypatch):
    calls = _install_fake_extractor(monkeypatch)
    clock = [5_000.0]
    monkeypatch.setattr(ytdlp_auth.time, "monotonic", lambda: clock[0])

    ydl = ytdlp_auth.RefugeYoutubeDL(
        {
            "format": "bestaudio/best",
            "noplaylist": True,
            "skip_download": True,
        }
    )
    url = "https://www.youtube.com/watch?v=test"

    first = ydl.extract_info(url, download=False)
    second = ydl.extract_info(url, download=False)
    assert first == second
    assert len(calls) == 1

    clock[0] += ytdlp_auth.YTDLP_DIRECT_CACHE_TTL_SECONDS + 1
    refreshed = ydl.extract_info(url, download=False)
    assert refreshed["id"] == "result-2"
    assert len(calls) == 2


def test_download_calls_bypass_result_cache(monkeypatch):
    calls = _install_fake_extractor(monkeypatch)
    ydl = ytdlp_auth.RefugeYoutubeDL({"format": "bestaudio/best"})
    url = "https://www.youtube.com/watch?v=test"

    ydl.extract_info(url, download=True)
    ydl.extract_info(url, download=True)

    assert len(calls) == 2


def test_cache_key_separates_different_extractor_options(monkeypatch):
    calls = _install_fake_extractor(monkeypatch)
    url = "https://www.youtube.com/watch?v=test"
    audio = ytdlp_auth.RefugeYoutubeDL({"format": "bestaudio/best"})
    flat = ytdlp_auth.RefugeYoutubeDL({"extract_flat": "in_playlist"})

    audio.extract_info(url, download=False)
    flat.extract_info(url, download=False)

    assert len(calls) == 2


def test_cache_is_bounded(monkeypatch):
    _install_fake_extractor(monkeypatch)
    monkeypatch.setattr(ytdlp_auth, "YTDLP_CACHE_MAX_ENTRIES", 2)
    ydl = ytdlp_auth.RefugeYoutubeDL({"extract_flat": "in_playlist"})

    ydl.extract_info("ytsearch1:first", download=False)
    ydl.extract_info("ytsearch1:second", download=False)
    ydl.extract_info("ytsearch1:third", download=False)

    assert len(ytdlp_auth._YTDLP_CACHE) == 2
