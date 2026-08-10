from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.feedback_portal as feedback


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_feedback_portal_uses_components_v2_and_preserves_contract() -> None:
    view = feedback.FeedbackPortalView(SimpleNamespace())

    assert isinstance(view, discord.ui.LayoutView)
    assert view.is_persistent()
    assert "## 📬 CENTRE DE RETOURS & SUPPORT" in _text(view)
    assert "### 💡 Proposer une idée" in _text(view)
    assert "### 🐛 Signaler un bug" in _text(view)
    assert "### ⭐ Donner un avis" in _text(view)

    buttons = _buttons(view)
    assert {button.custom_id for button in buttons} == feedback.PORTAL_CUSTOM_IDS
    assert {button.custom_id: button.label for button in buttons} == {
        "btn_suggestion": "Proposer une idée",
        "btn_bug": "Signaler un bug",
        "btn_avis": "Donner un avis",
    }


@pytest.mark.asyncio
async def test_feedback_portal_buttons_open_existing_modals() -> None:
    cog = SimpleNamespace()
    view = feedback.FeedbackPortalView(cog)
    buttons = {button.custom_id: button for button in _buttons(view)}

    expected = {
        "btn_suggestion": feedback.SuggestionModal,
        "btn_bug": feedback.BugReportModal,
        "btn_avis": feedback.OpinionModal,
    }

    for custom_id, modal_type in expected.items():
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_modal=AsyncMock())
        )
        await buttons[custom_id].callback(interaction)
        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, modal_type)
        assert modal.cog is cog


def test_feedback_portal_detection_supports_legacy_embed_and_nested_v2() -> None:
    legacy = SimpleNamespace(
        embeds=[discord.Embed(title=feedback.PORTAL_TITLE)],
        components=[],
    )
    assert feedback._is_portal_message(legacy)

    view = feedback.FeedbackPortalView(SimpleNamespace())
    modern = SimpleNamespace(embeds=[], components=view.children)
    assert feedback._is_portal_message(modern)

    unrelated = SimpleNamespace(
        embeds=[discord.Embed(title="Autre panneau")],
        components=[],
    )
    assert not feedback._is_portal_message(unrelated)


@pytest.mark.asyncio
async def test_render_portal_message_migrates_in_place() -> None:
    message = SimpleNamespace(edit=AsyncMock())
    cog = object.__new__(feedback.FeedbackPortalCog)
    cog.bot = SimpleNamespace()

    await feedback.FeedbackPortalCog._render_portal_message(cog, message)

    message.edit.assert_awaited_once()
    kwargs = message.edit.await_args.kwargs
    assert kwargs["content"] is None
    assert kwargs["embeds"] == []
    assert kwargs["attachments"] == []
    assert isinstance(kwargs["view"], discord.ui.LayoutView)


@pytest.mark.asyncio
async def test_ensure_portal_upgrades_legacy_message_without_duplicate(monkeypatch) -> None:
    legacy_message = SimpleNamespace(
        author=SimpleNamespace(id=999),
        embeds=[discord.Embed(title=feedback.PORTAL_TITLE)],
        components=[],
        edit=AsyncMock(),
    )

    class DummyChannel:
        def __init__(self) -> None:
            self.send = AsyncMock()

        def history(self, *, limit: int):
            assert limit == 50

            async def iterator():
                yield legacy_message

            return iterator()

    channel = DummyChannel()
    monkeypatch.setattr(feedback.discord.abc, "Messageable", DummyChannel)

    bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_channel=lambda _channel_id: channel,
    )
    cog = feedback.FeedbackPortalCog(bot)

    await cog.ensure_portal_message()

    legacy_message.edit.assert_awaited_once()
    channel.send.assert_not_awaited()


def test_feedback_staff_controls_are_unchanged() -> None:
    view = feedback.FeedbackStaffView(SimpleNamespace())
    assert {
        item.custom_id
        for item in view.children
        if isinstance(item, discord.ui.Button)
    } == {"staff_approve", "staff_reject", "staff_delete"}
