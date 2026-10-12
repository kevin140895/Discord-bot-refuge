from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

import cogs.community_goals as community_goals_module
from cogs.community_goals import CommunityGoalsCog
from ui.community_goals_view import CommunityGoalDisplay, CommunityGoalsView


def _container_text(view: CommunityGoalsView) -> str:
    container = view.children[0]
    return "\n".join(
        item.content
        for item in container.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_community_goals_view_renders_active_and_recent_goals() -> None:
    view = CommunityGoalsView(
        active_goals=(
            CommunityGoalDisplay(
                title="Semaine active",
                metric_emoji="💬",
                progress_value="500 messages",
                target_value="1000 messages",
                percent=50,
                progress_bar="█████░░░░░",
                deadline="<t:1786406400:R>",
                reward_text="Badge collectif",
                automation_note="🤖 Objectif automatique · difficulté **Normal**",
            ),
        ),
        completed_titles=("Objectif vocal", "Objectif XP"),
        automation_status="🤖 Automatisation en pause tant qu’un objectif est actif.",
    )

    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout is None
    assert len(view.children) == 1
    assert isinstance(view.children[0], discord.ui.Container)

    text = _container_text(view)
    assert "OBJECTIFS COMMUNAUTAIRES" in text
    assert "Semaine active" in text
    assert "█████░░░░░" in text
    assert "**50%**" in text
    assert "500 messages" in text
    assert "1000 messages" in text
    assert "<t:1786406400:R>" in text
    assert "Badge collectif" in text
    assert "Objectif automatique" in text
    assert "difficulté **Normal**" in text
    assert "Automatisation en pause" in text
    assert "Derniers objectifs réussis" in text
    assert "Objectif vocal" in text
    assert "Objectif XP" in text
    assert "Progression calculée depuis la création" in text

    assert not any(
        isinstance(item, (discord.ui.Button, discord.ui.Select))
        for item in view.walk_children()
    )


def test_community_goals_view_handles_no_active_goal() -> None:
    view = CommunityGoalsView(
        completed_titles=("Ancien objectif",),
        automation_status="🤖 Prochain tirage automatique : <t:1786406400:R>.",
    )
    text = _container_text(view)

    assert "Aucun objectif communautaire actif pour le moment." in text
    assert "Prochain tirage automatique" in text
    assert "<t:1786406400:R>" in text
    assert "Ancien objectif" in text


def test_community_goals_view_stays_under_components_limit_with_four_goals() -> None:
    goal = CommunityGoalDisplay(
        title="Objectif",
        metric_emoji="⭐",
        progress_value="100 XP",
        target_value="1000 XP",
        percent=10,
        progress_bar="█░░░░░░░░░",
        deadline="<t:1786406400:R>",
    )
    view = CommunityGoalsView(
        active_goals=(goal, goal, goal, goal),
        completed_titles=("A", "B", "C"),
        automation_status="🤖 Automatisation active.",
    )

    assert sum(1 for _item in view.walk_children()) < 40


@pytest.mark.asyncio
async def test_objectif_communaute_sends_components_v2_view(monkeypatch) -> None:
    active_goal = {
        "id": "goal-1",
        "metric_key": "messages",
        "target": 1000,
        "baseline_total": 100,
        "ends_at": "2026-08-12T12:00:00+00:00",
        "title": "Semaine active",
        "reward_text": "Badge collectif",
        "source": "automatic",
        "metadata": {"difficulty_label": "Ambitieux"},
        "status": "active",
    }
    completed_goal = {
        "id": "goal-2",
        "metric_key": "xp",
        "title": "Objectif XP",
        "status": "completed",
    }

    class FakeGoalStore:
        async def list_goals(self, *, status: str | None = None):
            if status == "active":
                return [active_goal]
            if status == "completed":
                return [completed_goal]
            return []

        async def get_automation_state(self):
            return {"next_goal_at": None, "had_active_goal": True}

    class FakeResponse:
        def __init__(self) -> None:
            self.kwargs = None

        async def send_message(self, **kwargs) -> None:
            self.kwargs = kwargs

    async def no_evaluation() -> None:
        return None

    async def fixed_progress(_goal) -> int:
        return 500

    monkeypatch.setattr(community_goals_module, "community_goal_store", FakeGoalStore())

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = CommunityGoalsCog(bot)
    monkeypatch.setattr(cog, "_evaluate_goals", no_evaluation)
    monkeypatch.setattr(cog, "_goal_progress", fixed_progress)

    response = FakeResponse()
    interaction = SimpleNamespace(response=response)
    await CommunityGoalsCog.objectif_communaute.callback(cog, interaction)

    assert response.kwargs is not None
    assert set(response.kwargs) == {"view"}
    view = response.kwargs["view"]
    assert isinstance(view, CommunityGoalsView)

    text = _container_text(view)
    assert "Semaine active" in text
    assert "█████░░░░░" in text
    assert "**50%**" in text
    assert "500 messages" in text
    assert "1000 messages" in text
    assert "Badge collectif" in text
    assert "Objectif XP" in text
    assert "Objectif automatique" in text
    assert "Ambitieux" in text
    assert "Automatisation en pause" in text


def _test_goal() -> dict:
    return {
        "id": "goal-automated-1",
        "metric_key": "messages",
        "title": "La place s'éveille",
        "target": 5,
        "baseline_total": 0,
        "ends_at": "2099-12-31T12:00:00+00:00",
        "source": "automatic",
        "metadata": {"duration_days": 3, "difficulty_label": "Normal"},
    }


@pytest.mark.asyncio
async def test_general_suppresses_goal_creation_and_completion_announcements(monkeypatch) -> None:
    monkeypatch.setattr(
        community_goals_module,
        "ANNOUNCE_CHANNEL_ID",
        community_goals_module.GENERAL_CHANNEL_ID,
    )
    bot = SimpleNamespace(get_channel=MagicMock(), fetch_channel=AsyncMock())
    cog = CommunityGoalsCog(bot)

    await cog._announce_automatic_goal(_test_goal())
    await cog._announce_completion(_test_goal(), progress=5)

    bot.get_channel.assert_not_called()
    bot.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_community_goals_are_created_and_completed_silently(monkeypatch) -> None:
    monkeypatch.setattr(
        community_goals_module,
        "ANNOUNCE_CHANNEL_ID",
        community_goals_module.GENERAL_CHANNEL_ID,
    )
    goal = _test_goal()
    store = SimpleNamespace(
        list_goals=AsyncMock(return_value=[goal]),
        finish_goal=AsyncMock(return_value=goal),
    )
    monkeypatch.setattr(community_goals_module, "community_goal_store", store)
    automation = SimpleNamespace(
        sync=AsyncMock(return_value=SimpleNamespace(created_goal=goal))
    )
    monkeypatch.setattr(
        community_goals_module, "community_goal_automation_service", automation
    )

    bot = SimpleNamespace(get_channel=MagicMock(), fetch_channel=AsyncMock())
    cog = CommunityGoalsCog(bot)
    cog._metric_total = AsyncMock(return_value=10)
    await CommunityGoalsCog.evaluate_community_goals.coro(cog)

    store.finish_goal.assert_awaited_once()
    automation.sync.assert_awaited_once()
    bot.get_channel.assert_not_called()
    bot.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_community_goals_still_announce_in_a_dedicated_channel(monkeypatch) -> None:
    dedicated_channel_id = 987654321
    monkeypatch.setattr(
        community_goals_module, "ANNOUNCE_CHANNEL_ID", dedicated_channel_id
    )
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = dedicated_channel_id
    channel.send = AsyncMock()
    bot = SimpleNamespace(
        get_channel=MagicMock(return_value=channel), fetch_channel=AsyncMock()
    )
    cog = CommunityGoalsCog(bot)

    await cog._announce_automatic_goal(_test_goal())
    await cog._announce_completion(_test_goal(), progress=5)

    assert channel.send.await_count == 2
    assert channel.send.await_args.kwargs["embed"].title == "🏆 Objectif communautaire atteint"
