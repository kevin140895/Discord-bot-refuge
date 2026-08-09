from cogs.refuge_panel import (
    DEFAULT_REFUGE_PANEL_CHANNEL_ID,
    panel_reference_needs_retirement,
    panel_refresh_action,
)
from models.refuge_world import RefugePanelState


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
