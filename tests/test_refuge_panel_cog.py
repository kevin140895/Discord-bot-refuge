from cogs.refuge_panel import panel_refresh_action


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
