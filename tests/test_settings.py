from __future__ import annotations

from pathlib import Path

import pytest

from settings import ConfigError, Settings


def test_settings_defaults_are_semantically_valid() -> None:
    settings = Settings.from_env({})

    assert 0 <= settings.casino_open_hour <= 23
    assert 0 <= settings.casino_close_hour <= 23
    assert settings.delete_delay_seconds >= 0
    assert settings.machine_a_sous_boundary_check_interval_minutes > 0
    assert settings.api_report_interval_min > 0
    assert settings.api_meter_persist_interval_seconds > 0
    assert settings.api_soft_limit_pct < settings.api_hard_limit_pct
    assert settings.announce_channel_id > 0
    assert settings.economy_channel_id > 0


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("CASINO_OPEN_HOUR", "24", "doit être <= 23"),
        ("CASINO_CLOSE_HOUR", "-1", "doit être >= 0"),
        ("DELETE_DELAY_SECONDS", "-1", "doit être >= 0"),
        (
            "MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES",
            "0",
            "doit être >= 1",
        ),
        ("API_REPORT_INTERVAL_MIN", "0", "doit être >= 1"),
        ("API_METER_PERSIST_INTERVAL_SECONDS", "0", "doit être >= 1"),
    ],
)
def test_settings_reject_semantically_invalid_numbers(
    name: str, value: str, expected: str
) -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env({name: value})

    message = str(exc_info.value)
    assert name in message
    assert expected in message


def test_settings_reject_invalid_integer_with_readable_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env({"CASINO_OPEN_HOUR": "matin"})

    message = str(exc_info.value)
    assert "Configuration invalide" in message
    assert "CASINO_OPEN_HOUR" in message
    assert "entier attendu" in message
    assert "ValueError" not in message


@pytest.mark.parametrize("soft,hard", [("95", "95"), ("96", "95")])
def test_settings_require_soft_api_limit_below_hard_limit(
    soft: str, hard: str
) -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env(
            {
                "API_SOFT_LIMIT_PCT": soft,
                "API_HARD_LIMIT_PCT": hard,
            }
        )

    assert "strictement inférieur" in str(exc_info.value)


def test_settings_require_positive_enabled_feature_channel_id() -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env(
            {
                "ENABLE_DAILY_AWARDS": "1",
                "AWARD_ANNOUNCE_CHANNEL_ID": "0",
            }
        )

    assert "AWARD_ANNOUNCE_CHANNEL_ID" in str(exc_info.value)
    assert "doit être >= 1" in str(exc_info.value)


def test_settings_allow_zero_channel_id_when_feature_is_disabled() -> None:
    settings = Settings.from_env(
        {
            "ENABLE_DAILY_AWARDS": "0",
            "AWARD_ANNOUNCE_CHANNEL_ID": "0",
            "ENABLE_GAME_LEVEL_FEED": "false",
            "LEVEL_FEED_CHANNEL_ID": "0",
        }
    )

    assert settings.enable_daily_awards is False
    assert settings.award_announce_channel_id == 0
    assert settings.enable_game_level_feed is False
    assert settings.level_feed_channel_id == 0


def test_settings_reject_invalid_boolean() -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env({"ENABLE_DAILY_AWARDS": "peut-etre"})

    assert "ENABLE_DAILY_AWARDS" in str(exc_info.value)
    assert "booléen attendu" in str(exc_info.value)


def test_settings_reject_invalid_timezone() -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env({"TZ": "Mars/Olympus"})

    assert "TZ" in str(exc_info.value)
    assert "fuseau horaire IANA invalide" in str(exc_info.value)


def test_settings_reject_inverted_double_xp_hours() -> None:
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env(
            {
                "XP_DOUBLE_VOICE_START_HOUR": "22",
                "XP_DOUBLE_VOICE_END_HOUR": "10",
            }
        )

    assert "XP_DOUBLE_VOICE_START_HOUR" in str(exc_info.value)
    assert "doit être <= XP_DOUBLE_VOICE_END_HOUR" in str(exc_info.value)


def test_config_has_no_direct_numeric_environment_casts() -> None:
    config_source = (Path(__file__).resolve().parents[1] / "config.py").read_text(
        encoding="utf-8"
    )

    assert "int(os.getenv" not in config_source
    assert "float(os.getenv" not in config_source
