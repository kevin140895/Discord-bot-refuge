"""Typed and validated application settings loaded from environment variables."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(RuntimeError):
    """Raised when one or more environment settings are invalid."""


def _read_int(
    env: Mapping[str, str],
    issues: list[str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = env.get(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw.strip())
        except (AttributeError, ValueError):
            issues.append(f"{name}: entier attendu, valeur reçue={raw!r}")
            return default

    if minimum is not None and value < minimum:
        issues.append(f"{name}: doit être >= {minimum}, valeur reçue={value}")
    if maximum is not None and value > maximum:
        issues.append(f"{name}: doit être <= {maximum}, valeur reçue={value}")
    return value


def _read_float(
    env: Mapping[str, str],
    issues: list[str],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = env.get(name)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw.strip())
        except (AttributeError, ValueError):
            issues.append(f"{name}: nombre attendu, valeur reçue={raw!r}")
            return default

    if not math.isfinite(value):
        issues.append(f"{name}: doit être un nombre fini, valeur reçue={value!r}")
        return default
    if minimum is not None and value < minimum:
        issues.append(f"{name}: doit être >= {minimum}, valeur reçue={value}")
    if maximum is not None and value > maximum:
        issues.append(f"{name}: doit être <= {maximum}, valeur reçue={value}")
    return value


def _read_bool(
    env: Mapping[str, str],
    issues: list[str],
    name: str,
    default: bool,
) -> bool:
    raw = env.get(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    issues.append(
        f"{name}: booléen attendu (1/0, true/false, yes/no, on/off), "
        f"valeur reçue={raw!r}"
    )
    return default


def _read_string(
    env: Mapping[str, str],
    issues: list[str],
    name: str,
    default: str,
    *,
    allow_empty: bool = False,
) -> str:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value and not allow_empty:
        issues.append(f"{name}: ne peut pas être vide")
        return default
    return value


def _resolve_data_dir(env: Mapping[str, str], issues: list[str]) -> str:
    raw = env.get("DATA_DIR")
    if raw is not None:
        value = raw.strip()
        if not value:
            issues.append("DATA_DIR: ne peut pas être vide")
        else:
            return value
    if os.path.isdir("/app/data"):
        return "/app/data"
    return "/data"


@dataclass(frozen=True, slots=True)
class Settings:
    """Environment-backed bot configuration.

    ``from_env`` validates both parsing and semantic constraints before the bot
    starts. Legacy module-level constants in :mod:`config` are aliases to the
    single loaded instance so existing cogs remain source-compatible.
    """

    trigger_channel_id: int
    guild_id: int
    tz: str
    casino_open_hour: int
    casino_close_hour: int
    economy_channel_id: int
    f1_channel_id: int
    vip_24h_role_id: int
    temp_voice_category_id: int
    delete_delay_seconds: int
    radio_stream_url: str
    radio_rap_fr_stream_url: str
    rock_radio_stream_url: str
    announce_channel_id: int
    award_announce_channel_id: int
    enable_daily_awards: bool
    level_feed_channel_id: int
    enable_game_level_feed: bool
    tiktok_announce_ch: int
    activity_summary_ch: int
    reminder_channel_id: int
    machine_a_sous_boundary_check_interval_minutes: int
    pari_xp_role_id: int
    data_dir: str
    xp_double_voice_duration_minutes: int
    xp_double_voice_start_hour: int
    xp_double_voice_end_hour: int
    xp_double_voice_announce_channel_id: int
    games_data_dir: str
    channel_rename_min_interval_per_channel: int
    channel_rename_min_interval_global: int
    channel_rename_debounce_seconds: int
    channel_rename_max_retries: int
    channel_rename_backoff_base: float
    owner_id: int
    bot_alerts_channel_id: int
    api_budget_per_10min: int
    api_soft_limit_pct: float
    api_hard_limit_pct: float
    api_slow_call_ms: int
    api_report_interval_min: int
    api_meter_persist_interval_seconds: int
    critical_log_channel_id: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source: Mapping[str, str] = os.environ if env is None else env
        issues: list[str] = []

        trigger_channel_id = _read_int(
            source, issues, "TRIGGER_CHANNEL_ID", 0, minimum=0
        )
        guild_id = _read_int(source, issues, "GUILD_ID", 0, minimum=0)

        tz = _read_string(source, issues, "TZ", "Europe/Paris")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            issues.append(f"TZ: fuseau horaire IANA invalide, valeur reçue={tz!r}")

        casino_open_hour = _read_int(
            source, issues, "CASINO_OPEN_HOUR", 10, minimum=0, maximum=23
        )
        casino_close_hour = _read_int(
            source, issues, "CASINO_CLOSE_HOUR", 6, minimum=0, maximum=23
        )

        economy_channel_id = _read_int(
            source,
            issues,
            "ECONOMY_CHANNEL_ID",
            1409633293791400108,
            minimum=1,
        )
        f1_channel_id = _read_int(source, issues, "F1_CHANNEL_ID", 0, minimum=0)
        vip_24h_role_id = _read_int(
            source, issues, "VIP_24H_ROLE_ID", 0, minimum=0
        )

        temp_voice_category_id = _read_int(
            source, issues, "TEMP_VOICE_CATEGORY_ID", 0, minimum=0
        )
        delete_delay_seconds = _read_int(
            source, issues, "DELETE_DELAY_SECONDS", 45, minimum=0
        )

        radio_stream_url = _read_string(
            source,
            issues,
            "RADIO_STREAM_URL",
            "https://stream.laut.fm/hiphop-forever",
        )
        radio_rap_fr_stream_url = _read_string(
            source,
            issues,
            "RADIO_RAP_FR_STREAM_URL",
            "https://icecast.skyrock.net/s/francais_aac_96k",
        )
        rock_radio_stream_url = _read_string(
            source,
            issues,
            "ROCK_RADIO_STREAM_URL",
            "https://stream.laut.fm/rockworld",
        )

        announce_channel_id = _read_int(
            source,
            issues,
            "ANNOUNCE_CHANNEL_ID",
            1400552164979507263,
            minimum=1,
        )

        enable_daily_awards = _read_bool(
            source, issues, "ENABLE_DAILY_AWARDS", True
        )
        award_announce_channel_id = _read_int(
            source,
            issues,
            "AWARD_ANNOUNCE_CHANNEL_ID",
            announce_channel_id,
            minimum=1 if enable_daily_awards else 0,
        )

        enable_game_level_feed = _read_bool(
            source, issues, "ENABLE_GAME_LEVEL_FEED", True
        )
        level_feed_channel_id = _read_int(
            source,
            issues,
            "LEVEL_FEED_CHANNEL_ID",
            1402419913716531352,
            minimum=1 if enable_game_level_feed else 0,
        )

        tiktok_announce_ch = _read_int(
            source,
            issues,
            "TIKTOK_ANNOUNCE_CH",
            announce_channel_id,
            minimum=1,
        )
        activity_summary_ch = _read_int(
            source,
            issues,
            "ACTIVITY_SUMMARY_CH",
            announce_channel_id,
            minimum=1,
        )
        reminder_channel_id = _read_int(
            source,
            issues,
            "REMINDER_CHANNEL_ID",
            announce_channel_id,
            minimum=1,
        )

        machine_a_sous_boundary_check_interval_minutes = _read_int(
            source,
            issues,
            "MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES",
            1,
            minimum=1,
        )
        pari_xp_role_id = _read_int(
            source, issues, "PARI_XP_ROLE_ID", 0, minimum=0
        )

        data_dir = _resolve_data_dir(source, issues)

        xp_double_voice_duration_minutes = _read_int(
            source,
            issues,
            "XP_DOUBLE_VOICE_DURATION_MINUTES",
            60,
            minimum=1,
        )
        xp_double_voice_start_hour = _read_int(
            source,
            issues,
            "XP_DOUBLE_VOICE_START_HOUR",
            10,
            minimum=0,
            maximum=23,
        )
        xp_double_voice_end_hour = _read_int(
            source,
            issues,
            "XP_DOUBLE_VOICE_END_HOUR",
            23,
            minimum=0,
            maximum=23,
        )
        if xp_double_voice_start_hour > xp_double_voice_end_hour:
            issues.append(
                "XP_DOUBLE_VOICE_START_HOUR: doit être <= "
                "XP_DOUBLE_VOICE_END_HOUR"
            )

        xp_double_voice_announce_channel_id = _read_int(
            source,
            issues,
            "XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID",
            announce_channel_id,
            minimum=1,
        )

        games_data_dir = _read_string(
            source,
            issues,
            "GAMES_DATA_DIR",
            os.path.join(data_dir, "games"),
        )

        channel_rename_min_interval_per_channel = _read_int(
            source,
            issues,
            "CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL",
            5,
            minimum=0,
        )
        channel_rename_min_interval_global = _read_int(
            source,
            issues,
            "CHANNEL_RENAME_MIN_INTERVAL_GLOBAL",
            2,
            minimum=0,
        )
        channel_rename_debounce_seconds = _read_int(
            source,
            issues,
            "CHANNEL_RENAME_DEBOUNCE_SECONDS",
            2,
            minimum=0,
        )
        channel_rename_max_retries = _read_int(
            source,
            issues,
            "CHANNEL_RENAME_MAX_RETRIES",
            5,
            minimum=0,
        )
        channel_rename_backoff_base = _read_float(
            source,
            issues,
            "CHANNEL_RENAME_BACKOFF_BASE",
            2.0,
            minimum=0.0,
        )

        owner_id = _read_int(
            source,
            issues,
            "OWNER_ID",
            541417878314942495,
            minimum=1,
        )

        bot_alerts_channel_id = _read_int(
            source, issues, "BOT_ALERTS_CHANNEL_ID", 0, minimum=0
        )
        api_budget_per_10min = _read_int(
            source, issues, "API_BUDGET_PER_10MIN", 10000, minimum=1
        )
        api_soft_limit_pct = _read_float(
            source,
            issues,
            "API_SOFT_LIMIT_PCT",
            85.0,
            minimum=0.0,
            maximum=100.0,
        )
        api_hard_limit_pct = _read_float(
            source,
            issues,
            "API_HARD_LIMIT_PCT",
            95.0,
            minimum=0.0,
            maximum=100.0,
        )
        if api_soft_limit_pct >= api_hard_limit_pct:
            issues.append(
                "API_SOFT_LIMIT_PCT: doit être strictement inférieur à "
                "API_HARD_LIMIT_PCT"
            )

        api_slow_call_ms = _read_int(
            source, issues, "API_SLOW_CALL_MS", 1000, minimum=0
        )
        api_report_interval_min = _read_int(
            source, issues, "API_REPORT_INTERVAL_MIN", 1, minimum=1
        )
        api_meter_persist_interval_seconds = _read_int(
            source,
            issues,
            "API_METER_PERSIST_INTERVAL_SECONDS",
            30,
            minimum=1,
        )
        critical_log_channel_id = _read_int(
            source, issues, "CRITICAL_LOG_CHANNEL_ID", 0, minimum=0
        )

        if issues:
            details = "\n".join(f" - {issue}" for issue in issues)
            raise ConfigError(f"Configuration invalide :\n{details}")

        return cls(
            trigger_channel_id=trigger_channel_id,
            guild_id=guild_id,
            tz=tz,
            casino_open_hour=casino_open_hour,
            casino_close_hour=casino_close_hour,
            economy_channel_id=economy_channel_id,
            f1_channel_id=f1_channel_id,
            vip_24h_role_id=vip_24h_role_id,
            temp_voice_category_id=temp_voice_category_id,
            delete_delay_seconds=delete_delay_seconds,
            radio_stream_url=radio_stream_url,
            radio_rap_fr_stream_url=radio_rap_fr_stream_url,
            rock_radio_stream_url=rock_radio_stream_url,
            announce_channel_id=announce_channel_id,
            award_announce_channel_id=award_announce_channel_id,
            enable_daily_awards=enable_daily_awards,
            level_feed_channel_id=level_feed_channel_id,
            enable_game_level_feed=enable_game_level_feed,
            tiktok_announce_ch=tiktok_announce_ch,
            activity_summary_ch=activity_summary_ch,
            reminder_channel_id=reminder_channel_id,
            machine_a_sous_boundary_check_interval_minutes=(
                machine_a_sous_boundary_check_interval_minutes
            ),
            pari_xp_role_id=pari_xp_role_id,
            data_dir=data_dir,
            xp_double_voice_duration_minutes=xp_double_voice_duration_minutes,
            xp_double_voice_start_hour=xp_double_voice_start_hour,
            xp_double_voice_end_hour=xp_double_voice_end_hour,
            xp_double_voice_announce_channel_id=(
                xp_double_voice_announce_channel_id
            ),
            games_data_dir=games_data_dir,
            channel_rename_min_interval_per_channel=(
                channel_rename_min_interval_per_channel
            ),
            channel_rename_min_interval_global=channel_rename_min_interval_global,
            channel_rename_debounce_seconds=channel_rename_debounce_seconds,
            channel_rename_max_retries=channel_rename_max_retries,
            channel_rename_backoff_base=channel_rename_backoff_base,
            owner_id=owner_id,
            bot_alerts_channel_id=bot_alerts_channel_id,
            api_budget_per_10min=api_budget_per_10min,
            api_soft_limit_pct=api_soft_limit_pct,
            api_hard_limit_pct=api_hard_limit_pct,
            api_slow_call_ms=api_slow_call_ms,
            api_report_interval_min=api_report_interval_min,
            api_meter_persist_interval_seconds=(
                api_meter_persist_interval_seconds
            ),
            critical_log_channel_id=critical_log_channel_id,
        )


__all__ = ["ConfigError", "Settings"]
