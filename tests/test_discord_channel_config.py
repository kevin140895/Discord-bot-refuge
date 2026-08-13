from pathlib import Path

from settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def test_active_feature_channel_ids_are_centralized_in_config() -> None:
    config_text = (ROOT / "config.py").read_text(encoding="utf-8")
    economy_text = (ROOT / "cogs" / "economy_ui.py").read_text(encoding="utf-8")
    f1_text = (ROOT / "cogs" / "f1_standings.py").read_text(encoding="utf-8")

    defaults = Settings.from_env({})
    assert defaults.economy_channel_id == 1409633293791400108
    assert defaults.f1_channel_id == 0
    assert "ECONOMY_CHANNEL_ID: int = SETTINGS.economy_channel_id" in config_text
    assert "F1_CHANNEL_ID: int = SETTINGS.f1_channel_id" in config_text

    assert "1409633293791400108" not in economy_text
    assert "CHANNEL_ID = config.ECONOMY_CHANNEL_ID" in economy_text

    assert "1413708410330939485" not in f1_text
    assert "from config import DATA_DIR, F1_CHANNEL_ID" in f1_text


def test_retired_and_legacy_cogs_are_disabled_from_auto_discovery() -> None:
    config_text = (ROOT / "config.py").read_text(encoding="utf-8")
    bot_text = (ROOT / "bot.py").read_text(encoding="utf-8")

    assert 'frozenset({"f1_standings", "nhl_notifications"})' in config_text
    assert "from config import DISABLED_COGS" in bot_text
    assert 'LEGACY_DISABLED_COGS: Final[frozenset[str]] = frozenset({"rock_radio"})' in bot_text
    assert "module.name in DISABLED_COGS or module.name in LEGACY_DISABLED_COGS" in bot_text
    assert "Skipping disabled cog" in bot_text
