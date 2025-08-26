import json

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog
from storage import economy


def test_shop_text_excludes_vip(tmp_path, monkeypatch):
    shop_file = tmp_path / "shop.json"
    shop_file.write_text(
        json.dumps(
            {
                "vip_24h": {"name": "VIP 24h", "price": 100},
                "ticket_royal": {"name": "Ticket", "price": 100},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(economy, "SHOP_FILE", shop_file)
    monkeypatch.setattr(economy_ui, "SHOP_FILE", shop_file)

    cog = EconomyUICog.__new__(EconomyUICog)
    text = EconomyUICog._build_shop_text(cog)
    assert "vip" not in text.lower()
    assert "ticket" in text.lower()
