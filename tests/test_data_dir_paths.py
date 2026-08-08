import os
from pathlib import Path

import config
from storage import economy


def test_economy_storage_is_rooted_under_data_dir():
    expected = Path(config.DATA_DIR) / "economy"

    assert economy.ECONOMY_DIR == expected
    assert economy.SHOP_FILE == expected / "shop.json"
    assert economy.TRANSACTIONS_FILE == expected / "transactions.json"
    assert economy.BOOSTS_FILE == expected / "boosts.json"
    assert economy.TICKETS_FILE == expected / "tickets.json"
    assert economy.UI_FILE == expected / "ui.json"


def test_games_storage_uses_data_dir_by_default_or_explicit_override():
    explicit = os.getenv("GAMES_DATA_DIR")
    expected = Path(explicit) if explicit else Path(config.DATA_DIR) / "games"

    assert Path(config.GAMES_DATA_DIR) == expected
