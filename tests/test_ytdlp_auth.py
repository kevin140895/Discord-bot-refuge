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


def test_external_pot_provider_uses_mweb_and_bgutil_http(tmp_path):
    provider_url = "http://youtube-pot.railway.internal:4416"
    config = ytdlp_auth.configure_ytdlp_auth(
        {
            "YOUTUBE_COOKIES_B64": _encoded_cookie(),
            "YOUTUBE_POT_PROVIDER_URL": provider_url,
        },
        temp_dir=str(tmp_path),
    )

    options = ytdlp_auth.augment_ytdlp_options({"quiet": True})

    assert config.pot_provider_url == provider_url
    assert options["extractor_args"] == {
        "youtube": {"player_client": ["mweb"]},
        "youtubepot-bgutilhttp": {"base_url": [provider_url]},
    }


def test_external_pot_provider_preserves_explicit_extractor_args(tmp_path):
    provider_url = "http://youtube-pot.railway.internal:4416"
    ytdlp_auth.configure_ytdlp_auth(
        {"YOUTUBE_POT_PROVIDER_URL": provider_url},
        temp_dir=str(tmp_path),
    )

    options = ytdlp_auth.augment_ytdlp_options(
        {
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_safari"],
                    "player_skip": ["configs"],
                },
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://custom-provider.internal:4416"]
                },
            }
        }
    )

    assert options["extractor_args"]["youtube"] == {
        "player_client": ["web_safari"],
        "player_skip": ["configs"],
    }
    assert options["extractor_args"]["youtubepot-bgutilhttp"] == {
        "base_url": ["http://custom-provider.internal:4416"]
    }


def test_invalid_pot_provider_url_is_ignored_without_breaking_cookie_auth(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    config = ytdlp_auth.load_ytdlp_auth_config(
        {
            "YOUTUBE_COOKIES_B64": _encoded_cookie(),
            "YOUTUBE_POT_PROVIDER_URL": "file:///tmp/not-http",
        },
        temp_dir=str(tmp_path),
    )

    assert config.cookiefile is not None
    assert config.pot_provider_url is None
    assert "YOUTUBE_POT_PROVIDER_URL inutilisable" in caplog.text


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


def test_metadata_cache_keeps_only_stable_fields(monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr(ytdlp_auth.time, "monotonic", lambda: clock[0])
    ytdlp_auth.clear_ytdlp_metadata_cache()

    target = "https://www.youtube.com/watch?v=abc123"
    key = ytdlp_auth.make_ytdlp_metadata_cache_key("url", target)
    stored = ytdlp_auth.set_ytdlp_metadata_cache(
        key,
        {
            "id": "abc123",
            "title": "Cached title",
            "webpage_url": target,
            "duration": 123,
            "uploader": "Example channel",
            "url": "https://signed-media.invalid/stream",
            "formats": [{"url": "https://signed-media.invalid/format"}],
            "http_headers": {"Authorization": "temporary"},
        },
    )

    assert stored is True
    cached = ytdlp_auth.get_ytdlp_metadata_cache(key)
    assert cached == {
        "id": "abc123",
        "title": "Cached title",
        "webpage_url": target,
        "duration": 123,
        "uploader": "Example channel",
    }
    assert "url" not in cached
    assert "formats" not in cached
    assert "http_headers" not in cached


def test_metadata_cache_expires_after_one_hour(monkeypatch):
    clock = [5_000.0]
    monkeypatch.setattr(ytdlp_auth.time, "monotonic", lambda: clock[0])
    ytdlp_auth.clear_ytdlp_metadata_cache()

    target = "https://www.youtube.com/watch?v=ttl"
    key = ytdlp_auth.make_ytdlp_metadata_cache_key("url", target)
    assert ytdlp_auth.set_ytdlp_metadata_cache(
        key,
        {"title": "TTL", "webpage_url": target},
    )
    assert ytdlp_auth.get_ytdlp_metadata_cache(key) is not None

    clock[0] += ytdlp_auth.YTDLP_METADATA_CACHE_TTL_SECONDS + 1
    assert ytdlp_auth.get_ytdlp_metadata_cache(key) is None


def test_search_cache_key_normalises_case_and_whitespace():
    left = ytdlp_auth.make_ytdlp_metadata_cache_key(
        "search", "  Daft   Punk   One More Time  "
    )
    right = ytdlp_auth.make_ytdlp_metadata_cache_key(
        "search", "daft punk one more time"
    )

    assert left == right


def test_metadata_cache_is_bounded(monkeypatch):
    ytdlp_auth.clear_ytdlp_metadata_cache()
    monkeypatch.setattr(ytdlp_auth, "YTDLP_METADATA_CACHE_MAX_ENTRIES", 2)

    keys = []
    for index in range(3):
        target = f"https://www.youtube.com/watch?v={index}"
        key = ytdlp_auth.make_ytdlp_metadata_cache_key("url", target)
        keys.append(key)
        assert ytdlp_auth.set_ytdlp_metadata_cache(
            key,
            {"title": f"Track {index}", "webpage_url": target},
        )

    assert len(ytdlp_auth._YTDLP_METADATA_CACHE) == 2
    assert ytdlp_auth.get_ytdlp_metadata_cache(keys[0]) is None
    assert ytdlp_auth.get_ytdlp_metadata_cache(keys[1]) is not None
    assert ytdlp_auth.get_ytdlp_metadata_cache(keys[2]) is not None


def test_metadata_cache_can_use_direct_url_as_fallback():
    ytdlp_auth.clear_ytdlp_metadata_cache()
    target = "https://www.youtube.com/watch?v=fallback"
    key = ytdlp_auth.make_ytdlp_metadata_cache_key("url", target)

    assert ytdlp_auth.set_ytdlp_metadata_cache(
        key,
        {"id": "fallback", "title": "Fallback"},
        fallback_url=target,
    )
    assert ytdlp_auth.get_ytdlp_metadata_cache(key)["webpage_url"] == target
