from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_feature_channel_ids_are_centralized_in_config() -> None:
    config_text = (ROOT / "config.py").read_text(encoding="utf-8")
    economy_text = (ROOT / "cogs" / "economy_ui.py").read_text(encoding="utf-8")
    f1_text = (ROOT / "cogs" / "f1_standings.py").read_text(encoding="utf-8")

    assert '"ECONOMY_CHANNEL_ID", 1409633293791400108' in config_text
    assert '"F1_CHANNEL_ID", 1413708410330939485' in config_text

    assert "1409633293791400108" not in economy_text
    assert "CHANNEL_ID = config.ECONOMY_CHANNEL_ID" in economy_text

    assert "1413708410330939485" not in f1_text
    assert "from config import DATA_DIR, F1_CHANNEL_ID" in f1_text
