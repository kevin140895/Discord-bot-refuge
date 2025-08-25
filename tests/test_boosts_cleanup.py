import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from cogs.economy_ui import EconomyUICog
from storage import economy
import config


@pytest.mark.asyncio
async def test_cleanup_boosts(tmp_path, monkeypatch):
    boosts_file = tmp_path / "boosts.json"
    monkeypatch.setattr(economy, "BOOSTS_FILE", boosts_file)

    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    boosts_file.write_text(
        json.dumps(
            {
                "1": [
                    {"type": "double_xp", "until": expired},
                    {"type": "vip", "until": expired},
                ],
                "2": [{"type": "vip", "until": future}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "VIP_24H_ROLE_ID", 42)
    monkeypatch.setattr(config, "GUILD_ID", 321)

    role = SimpleNamespace(id=42)
    member = SimpleNamespace(id=1, remove_roles=AsyncMock(), roles=[role])
    guild = SimpleNamespace(
        get_member=lambda uid: member if int(uid) == 1 else None,
        get_role=lambda rid: role if rid == 42 else None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 321 else None)

    cog = EconomyUICog(bot)

    await cog._cleanup_boosts_once()

    member.remove_roles.assert_awaited_once_with(role, reason="Boost expiré")
    boosts = economy.load_boosts()
    assert "1" not in boosts
    assert "2" in boosts
