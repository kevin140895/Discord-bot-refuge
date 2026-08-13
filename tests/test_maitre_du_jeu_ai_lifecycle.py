import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.maitre_du_jeu import MaitreDuJeuCog, build_ai_response
from cogs.maitre_du_jeu_ai import AIReply, AIStatus, MaitreDuJeuAI


def _success_payload(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class _FakeResponse:
    def __init__(self, text: str = "Réponse persistante.") -> None:
        self.status = 200
        self.headers = {"x-request-id": "req-persistent"}
        self._body = json.dumps(_success_payload(text))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def text(self) -> str:
        return self._body


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, *, json: dict):
        self.posts.append((url, json))
        return _FakeResponse()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_ai_global_quota_is_shared_across_users_and_rolls_over():
    clock = [100.0]
    requester = AsyncMock(
        side_effect=[
            (200, _success_payload("Première réponse."), {}),
            (200, _success_payload("Après fenêtre."), {}),
        ]
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        user_window_seconds=60,
        max_requests_per_window=10,
        global_window_seconds=60,
        global_max_requests_per_window=1,
    )

    first = await ai.answer(1, "Question 1")
    clock[0] += 1
    blocked = await ai.answer(2, "Question 2")
    clock[0] += 60
    released = await ai.answer(2, "Question 3")

    assert first.status is AIStatus.SUCCESS
    assert blocked.status is AIStatus.GLOBAL_QUOTA
    assert blocked.retry_after == pytest.approx(59.0)
    assert released.status is AIStatus.SUCCESS
    assert requester.await_count == 2


@pytest.mark.asyncio
async def test_user_quota_rejection_does_not_consume_global_capacity():
    clock = [100.0]
    requester = AsyncMock(
        side_effect=[
            (200, _success_payload("Utilisateur 1."), {}),
            (200, _success_payload("Utilisateur 2."), {}),
        ]
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        user_window_seconds=60,
        max_requests_per_window=1,
        global_window_seconds=60,
        global_max_requests_per_window=2,
    )

    first = await ai.answer(1, "Question 1")
    clock[0] += 1
    user_blocked = await ai.answer(1, "Question 2")
    second_user = await ai.answer(2, "Question 3")

    assert first.status is AIStatus.SUCCESS
    assert user_blocked.status is AIStatus.QUOTA
    assert second_user.status is AIStatus.SUCCESS
    assert requester.await_count == 2


@pytest.mark.asyncio
async def test_ai_reuses_one_http_session_and_closes_it_idempotently():
    sessions: list[tuple[_FakeSession, dict]] = []

    def session_factory(**kwargs):
        session = _FakeSession()
        sessions.append((session, kwargs))
        return session

    ai = MaitreDuJeuAI(
        api_key="mistral-test-key",
        session_factory=session_factory,
        user_cooldown_seconds=0,
        max_requests_per_window=10,
        global_max_requests_per_window=10,
    )

    first = await ai.answer(1, "Question 1")
    second = await ai.answer(2, "Question 2")

    assert first.status is AIStatus.SUCCESS
    assert second.status is AIStatus.SUCCESS
    assert len(sessions) == 1
    session, kwargs = sessions[0]
    assert len(session.posts) == 2
    assert kwargs["headers"]["Authorization"] == "Bearer mistral-test-key"
    assert kwargs["headers"]["Accept"] == "application/json"

    await ai.aclose()
    await ai.aclose()

    assert session.closed is True
    assert ai.available is False


@pytest.mark.asyncio
async def test_maitre_du_jeu_cog_unload_closes_ai_client():
    cog = MaitreDuJeuCog(MagicMock())
    cog.ai.aclose = AsyncMock()

    await cog.cog_unload()

    cog.ai.aclose.assert_awaited_once_with()


def test_global_quota_has_distinct_discord_message():
    embed = build_ai_response(
        AIReply(AIStatus.GLOBAL_QUOTA, retry_after=60.0)
    )

    assert embed.title == "🛡️ Maître du jeu · Capacité IA"
    assert "limite globale" in (embed.description or "").casefold()


def test_env_example_documents_global_mistral_limits():
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "MISTRAL_GLOBAL_WINDOW_SECONDS=3600.0" in env_example
    assert "MISTRAL_GLOBAL_MAX_REQUESTS_PER_WINDOW=100" in env_example
