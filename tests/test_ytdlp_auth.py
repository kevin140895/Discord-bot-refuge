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
