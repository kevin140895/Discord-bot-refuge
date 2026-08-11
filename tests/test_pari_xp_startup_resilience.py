from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import cogs.pari_xp as pari_xp


def _make_cog():
    cog = object.__new__(pari_xp.PariXPCog)
    cog.bot = SimpleNamespace(add_view=MagicMock())
    cog.state = {"is_open": True, "total_bets": 0, "total_winnings": 0}
    cog.is_open = True
    cog._message_id = 123
    cog._last_announced_state = None
    cog._last_panel_signature = None
    return cog


@pytest.mark.asyncio
async def test_cog_load_survives_discord_http_error_during_panel_sync(monkeypatch, caplog):
    """A transient Discord 503 must never abort extension loading."""

    class FakeHTTPException(Exception):
        pass

    monkeypatch.setattr(pari_xp.discord, "HTTPException", FakeHTTPException)
    cog = _make_cog()
    cog._ensure_roulette_message_once = AsyncMock(
        side_effect=FakeHTTPException("503 Service Unavailable")
    )

    with caplog.at_level("WARNING"):
        await cog.cog_load()

    cog._ensure_roulette_message_once.assert_awaited_once()
    assert "Synchronisation du panneau différée" in caplog.text


@pytest.mark.asyncio
async def test_panel_sync_does_not_hide_programming_errors():
    """Only Discord HTTP failures are downgraded to a retryable warning."""

    cog = _make_cog()
    cog._ensure_roulette_message_once = AsyncMock(
        side_effect=RuntimeError("programming bug")
    )

    with pytest.raises(RuntimeError, match="programming bug"):
        await cog._ensure_roulette_message()
