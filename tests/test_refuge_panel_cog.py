from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from cogs.refuge_panel import (
    DEFAULT_REFUGE_PANEL_CHANNEL_ID,
    RefugePanelCog,
    panel_reference_needs_retirement,
    panel_refresh_action,
)
from models.refuge_world import RefugeHistoricalEvent, RefugePanelState, RefugeWorldState
from services.refuge_world_coordination import refuge_world_mutation_lock


def test_default_public_panel_channel_is_refuge_channel():
    assert DEFAULT_REFUGE_PANEL_CHANNEL_ID == 1536027732071161987


def test_refresh_creates_when_message_is_missing():
    assert panel_refresh_action(
        message_exists=False,
        previous_visual_signature="v1",
        previous_summary_signature="s1",
        visual_signature="v1",
        summary_signature="s1",
    ) == "create"


def test_first_observation_after_restart_forces_render_refresh():
    assert panel_refresh_action(
        message_exists=True,
        previous_visual_signature=None,
        previous_summary_signature=None,
        visual_signature="v1",
        summary_signature="s1",
    ) == "render"


def test_visual_change_requires_rerender():
    assert panel_refresh_action(
        message_exists=True,
        previous_visual_signature="v1",
        previous_summary_signature="s1",
        visual_signature="v2",
        summary_signature="s1",
    ) == "render"


def test_summary_only_change_reuses_attachment():
    assert panel_refresh_action(
        message_exists=True,
        previous_visual_signature="v1",
        previous_summary_signature="s1",
        visual_signature="v1",
        summary_signature="s2",
    ) == "summary"


def test_identical_state_does_nothing():
    assert panel_refresh_action(
        message_exists=True,
        previous_visual_signature="v1",
        previous_summary_signature="s1",
        visual_signature="v1",
        summary_signature="s1",
    ) == "none"


def test_panel_reference_is_retired_only_when_configured_channel_moves():
    panel = RefugePanelState(channel_id=123, message_id=456)
    assert panel_reference_needs_retirement(panel, target_channel_id=789) is True
    assert panel_reference_needs_retirement(panel, target_channel_id=123) is False
    assert panel_reference_needs_retirement(
        RefugePanelState(channel_id=123, message_id=None),
        target_channel_id=789,
    ) is False


class _LockCheckingWorldStore:
    def __init__(self, state: RefugeWorldState) -> None:
        self.state = state
        self.get_calls = 0
        self.save_calls = 0

    async def get_state(self) -> RefugeWorldState:
        assert refuge_world_mutation_lock().locked()
        self.get_calls += 1
        return self.state

    async def save_state(self, state: RefugeWorldState) -> RefugeWorldState:
        assert refuge_world_mutation_lock().locked()
        self.save_calls += 1
        self.state = state
        return state


@pytest.mark.asyncio
async def test_panel_reference_read_modify_write_uses_world_mutation_lock():
    history = RefugeHistoricalEvent(
        event_id="secret",
        event_type="fire_secret_discovered",
        occurred_at="2026-08-09T17:00:00+00:00",
        data={"name": "Mystère"},
    )
    store = _LockCheckingWorldStore(RefugeWorldState(events=(history,)))
    cog = RefugePanelCog(SimpleNamespace(), world_store=store)  # type: ignore[arg-type]
    message = SimpleNamespace(
        id=456,
        channel=SimpleNamespace(id=123),
    )

    await cog._persist_panel_reference(message)  # type: ignore[arg-type]

    assert store.get_calls == 1
    assert store.save_calls == 1
    assert store.state.panel == RefugePanelState(channel_id=123, message_id=456)
    assert store.state.events == (history,)


class _NeverEvaluatePanelService:
    async def evaluate(self):
        raise AssertionError("panel evaluation must not run on uncertain lookup")


@pytest.mark.asyncio
async def test_uncertain_message_lookup_never_creates_replacement_panel():
    cog = RefugePanelCog(
        SimpleNamespace(),  # type: ignore[arg-type]
        panel_service=_NeverEvaluatePanelService(),  # type: ignore[arg-type]
    )
    channel = SimpleNamespace(id=123)

    async def resolve_channel():
        return channel

    async def retire_previous_panel_if_moved(*, target_channel_id: int):
        assert target_channel_id == 123
        return True

    async def fetch_stored_message(_channel):
        return None, False

    cog._resolve_channel = resolve_channel  # type: ignore[method-assign]
    cog._retire_previous_panel_if_moved = retire_previous_panel_if_moved  # type: ignore[method-assign]
    cog._fetch_stored_message = fetch_stored_message  # type: ignore[method-assign]

    await cog.ensure_panel()


def _http_exception(exc_type, *, status: int):
    error = object.__new__(exc_type)
    Exception.__init__(error, "temporary Discord failure")
    error.status = status
    error.reason = "temporary"
    error.code = 0
    error.text = "temporary"
    return error


class _PanelStateStore:
    def __init__(self, panel: RefugePanelState) -> None:
        self.state = RefugeWorldState(panel=panel)

    async def get_state(self) -> RefugeWorldState:
        return self.state


@pytest.mark.asyncio
async def test_transient_discord_fetch_is_not_treated_as_missing_message():
    store = _PanelStateStore(RefugePanelState(channel_id=123, message_id=456))
    cog = RefugePanelCog(
        SimpleNamespace(),  # type: ignore[arg-type]
        world_store=store,  # type: ignore[arg-type]
    )

    class Channel:
        id = 123

        async def fetch_message(self, message_id: int):
            assert message_id == 456
            raise _http_exception(discord.HTTPException, status=503)

    message, definitive = await cog._fetch_stored_message(Channel())  # type: ignore[arg-type]

    assert message is None
    assert definitive is False


@pytest.mark.asyncio
async def test_confirmed_not_found_allows_safe_panel_recreation():
    store = _PanelStateStore(RefugePanelState(channel_id=123, message_id=456))
    cog = RefugePanelCog(
        SimpleNamespace(),  # type: ignore[arg-type]
        world_store=store,  # type: ignore[arg-type]
    )

    class Channel:
        id = 123

        async def fetch_message(self, message_id: int):
            assert message_id == 456
            raise _http_exception(discord.NotFound, status=404)

    message, definitive = await cog._fetch_stored_message(Channel())  # type: ignore[arg-type]

    assert message is None
    assert definitive is True
