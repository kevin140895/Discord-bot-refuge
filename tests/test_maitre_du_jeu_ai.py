from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.maitre_du_jeu_ai import AIReply, AIStatus, MaitreDuJeuAI


def _fake_client(*responses):
    create = AsyncMock(side_effect=list(responses))
    return SimpleNamespace(responses=SimpleNamespace(create=create)), create


@pytest.mark.asyncio
async def test_ai_success_uses_responses_api_without_tools_or_storage():
    client, create = _fake_client(SimpleNamespace(output_text="Bonjour depuis l'IA."))
    ai = MaitreDuJeuAI(
        client=client,
        model="gpt-5-mini",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(42, "Raconte-moi une anecdote.")

    assert reply == AIReply(AIStatus.SUCCESS, text="Bonjour depuis l'IA.")
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    assert kwargs["store"] is False
    assert kwargs["max_output_tokens"] == ai.max_output_tokens
    assert "lecture seule" in kwargs["instructions"]
    assert "tools" not in kwargs
    assert kwargs["input"] == [
        {"role": "user", "content": "Raconte-moi une anecdote."}
    ]


@pytest.mark.asyncio
async def test_ai_keeps_only_short_local_conversation_context():
    clock = [100.0]
    client, create = _fake_client(
        SimpleNamespace(output_text="Première réponse."),
        SimpleNamespace(output_text="Deuxième réponse."),
    )
    ai = MaitreDuJeuAI(
        client=client,
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        memory_max_messages=4,
        max_requests_per_window=5,
    )

    first = await ai.answer(7, "Première question")
    clock[0] += 5
    second = await ai.answer(7, "Deuxième question")

    assert first.status is AIStatus.SUCCESS
    assert second.status is AIStatus.SUCCESS
    second_input = create.await_args_list[1].kwargs["input"]
    assert second_input == [
        {"role": "user", "content": "Première question"},
        {"role": "assistant", "content": "Première réponse."},
        {"role": "user", "content": "Deuxième question"},
    ]


@pytest.mark.asyncio
async def test_ai_memory_expires_after_ttl():
    clock = [100.0]
    client, create = _fake_client(
        SimpleNamespace(output_text="Première réponse."),
        SimpleNamespace(output_text="Nouvelle réponse."),
    )
    ai = MaitreDuJeuAI(
        client=client,
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        memory_ttl_seconds=10,
        max_requests_per_window=5,
    )

    await ai.answer(7, "Première question")
    clock[0] += 11
    await ai.answer(7, "Question après expiration")

    assert create.await_args_list[1].kwargs["input"] == [
        {"role": "user", "content": "Question après expiration"}
    ]


@pytest.mark.asyncio
async def test_ai_enforces_per_user_cooldown_before_second_api_call():
    clock = [100.0]
    client, create = _fake_client(SimpleNamespace(output_text="Réponse."))
    ai = MaitreDuJeuAI(
        client=client,
        clock=lambda: clock[0],
        user_cooldown_seconds=8,
        max_requests_per_window=5,
    )

    first = await ai.answer(7, "Question 1")
    clock[0] += 3
    second = await ai.answer(7, "Question 2")

    assert first.status is AIStatus.SUCCESS
    assert second.status is AIStatus.COOLDOWN
    assert second.retry_after == pytest.approx(5.0)
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_ai_enforces_rolling_request_quota():
    clock = [100.0]
    client, create = _fake_client(SimpleNamespace(output_text="Réponse."))
    ai = MaitreDuJeuAI(
        client=client,
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        user_window_seconds=60,
        max_requests_per_window=1,
    )

    first = await ai.answer(7, "Question 1")
    clock[0] += 1
    second = await ai.answer(7, "Question 2")

    assert first.status is AIStatus.SUCCESS
    assert second.status is AIStatus.QUOTA
    assert second.retry_after == pytest.approx(59.0)
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_ai_unavailable_without_api_key_never_attempts_request():
    ai = MaitreDuJeuAI(api_key="")

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_ai_api_failure_returns_safe_error_status():
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
    )
    ai = MaitreDuJeuAI(
        client=client,
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.ERROR
    assert reply.text is None


@pytest.mark.asyncio
async def test_ai_rejects_oversized_question_before_api_call():
    client, create = _fake_client(SimpleNamespace(output_text="Ne doit pas être appelée"))
    ai = MaitreDuJeuAI(
        client=client,
        question_max_chars=10,
        user_cooldown_seconds=0,
    )

    reply = await ai.answer(7, "Cette question est beaucoup trop longue")

    assert reply.status is AIStatus.TOO_LONG
    create.assert_not_awaited()
