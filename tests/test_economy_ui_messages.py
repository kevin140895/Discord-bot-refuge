import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog
from storage import economy


@pytest.mark.asyncio
async def test_ui_messages_recreated(tmp_path, monkeypatch):
    # Patch file paths to temporary directory
    monkeypatch.setattr(economy, "ECONOMY_DIR", tmp_path)
    monkeypatch.setattr(economy_ui, "ECONOMY_DIR", tmp_path)
    ui_file = tmp_path / "ui.json"
    ui_file.write_text(json.dumps({"shop_message_id": 1}), encoding="utf-8")
    monkeypatch.setattr(economy, "UI_FILE", ui_file)
    monkeypatch.setattr(economy_ui, "CHANNEL_ID", 123)

    # Dummy channel behaving like discord.TextChannel
    class DummyMessage(SimpleNamespace):
        pass

    class DummyChannel:
        def __init__(self):
            self.sent = []

        async def fetch_message(self, message_id):
            raise Exception("missing")

        async def send(self, content, view):
            msg = DummyMessage(id=100 + len(self.sent), pin=AsyncMock(), edit=AsyncMock())
            self.sent.append(msg)
            return msg

    channel = DummyChannel()
    # Make isinstance check succeed
    monkeypatch.setattr(economy_ui.discord, "TextChannel", DummyChannel)

    bot = SimpleNamespace(
        get_channel=lambda cid: channel if cid == 123 else None,
        add_view=lambda view: None,
        wait_until_ready=AsyncMock(),
    )

    cog = EconomyUICog(bot)
    await cog.cog_load()

    data = json.loads(ui_file.read_text(encoding="utf-8"))
    assert data["shop_message_id"] == channel.sent[0].id
    assert len(channel.sent) == 1
    for msg in channel.sent:
        msg.pin.assert_awaited_once()
