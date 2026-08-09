from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import cogs.pari_xp as pari_xp
import cogs.machine_a_sous.machine_a_sous as machine_a_sous
from cogs.machine_a_sous.machine_a_sous import (
    MachineASousCog,
    MachineASousView,
    _is_machine_poster_message,
    _poster_has_play_button,
    _poster_is_components_v2,
)


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def _roulette_cog(*, is_open: bool) -> pari_xp.PariXPCog:
    cog = object.__new__(pari_xp.PariXPCog)
    cog.state = {
        "is_open": is_open,
        "total_bets": 120,
        "total_winnings": 80,
        "last_winner": {"user_id": 42, "amount": 40},
    }
    cog.is_open = is_open
    return cog


def test_roulette_v2_closed_state_hides_all_bet_buttons() -> None:
    view = pari_xp.RouletteXPView(_roulette_cog(is_open=False), disabled=True)

    assert isinstance(view, discord.ui.LayoutView)
    assert _text(view) == "🔴 **Casino fermé**"
    assert _buttons(view) == []


def test_slot_machine_v2_open_and_closed_posters_keep_same_play_contract() -> None:
    opened = MachineASousView(enabled=True)
    closed = MachineASousView(enabled=False)

    assert isinstance(opened, discord.ui.LayoutView)
    assert isinstance(closed, discord.ui.LayoutView)
    assert "✅ **Ouverte**" in _text(opened)
    assert "⛔ **Fermée**" in _text(closed)

    assert [button.custom_id for button in _buttons(opened)] == ["machineasous:play"]
    assert _buttons(closed) == []


def test_slot_poster_detection_distinguishes_legacy_and_components_v2() -> None:
    v2_view = MachineASousView(enabled=True)
    v2_message = SimpleNamespace(embeds=[], components=v2_view.children)

    assert _is_machine_poster_message(v2_message)
    assert _poster_is_components_v2(v2_message)
    assert _poster_has_play_button(v2_message)

    legacy_message = SimpleNamespace(
        embeds=[SimpleNamespace(title="🎰 Machine à sous")],
        components=[],
    )
    assert _is_machine_poster_message(legacy_message)
    assert not _poster_is_components_v2(legacy_message)
    assert not _poster_has_play_button(legacy_message)


@pytest.mark.asyncio
async def test_discovered_legacy_slot_poster_is_registered_before_replacement(
    monkeypatch,
) -> None:
    class FakeTextChannel:
        id = machine_a_sous.CHANNEL_ID

        async def history(self, *, limit: int):
            assert limit == 20
            yield legacy_message

    class FakeThread:
        pass

    channel = FakeTextChannel()
    legacy_message = SimpleNamespace(
        id=321,
        author=SimpleNamespace(id=999),
        channel=channel,
        embeds=[SimpleNamespace(title="🎰 Machine à sous")],
        components=[],
    )
    bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_channel=lambda channel_id: channel,
    )
    store = SimpleNamespace(
        get_poster=Mock(return_value=None),
        set_poster=Mock(),
    )
    cog = object.__new__(MachineASousCog)
    cog.bot = bot
    cog.store = store
    cog.current_view_enabled = True

    async def replace_after_registration() -> None:
        store.set_poster.assert_called_once_with(
            channel_id=str(channel.id),
            message_id=str(legacy_message.id),
        )

    cog._replace_poster_message = AsyncMock(side_effect=replace_after_registration)

    monkeypatch.setattr(machine_a_sous.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(machine_a_sous.discord, "Thread", FakeThread)

    await MachineASousCog._ensure_poster_message(cog)

    cog._replace_poster_message.assert_awaited_once()
