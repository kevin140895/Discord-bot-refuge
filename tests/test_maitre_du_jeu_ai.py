from unittest.mock import AsyncMock

import pytest

from cogs.maitre_du_jeu_ai import AIReply, AIStatus, MaitreDuJeuAI


def _success_payload(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _fake_requester(*responses):
    return AsyncMock(side_effect=list(responses))


@pytest.mark.asyncio
async def test_ai_success_uses_mistral_chat_without_tools():
    requester = _fake_requester(
        (200, _success_payload("Bonjour depuis Mistral."), {"x-request-id": "req-1"})
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        model="mistral-small-latest",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(42, "Raconte-moi une anecdote.")

    assert reply == AIReply(AIStatus.SUCCESS, text="Bonjour depuis Mistral.")
    requester.assert_awaited_once()
    payload = requester.await_args.args[0]
    assert payload["model"] == "mistral-small-latest"
    assert payload["max_tokens"] == ai.max_output_tokens
    assert payload["tool_choice"] == "none"
    assert payload["safe_prompt"] is True
    assert "tools" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert "lecture seule" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {
        "role": "user",
        "content": "Raconte-moi une anecdote.",
    }


@pytest.mark.asyncio
async def test_ai_keeps_only_short_local_conversation_context():
    clock = [100.0]
    requester = _fake_requester(
        (200, _success_payload("Première réponse."), {}),
        (200, _success_payload("Deuxième réponse."), {}),
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
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
    second_messages = requester.await_args_list[1].args[0]["messages"]
    assert second_messages[0]["role"] == "system"
    assert second_messages[1:] == [
        {"role": "user", "content": "Première question"},
        {"role": "assistant", "content": "Première réponse."},
        {"role": "user", "content": "Deuxième question"},
    ]


@pytest.mark.asyncio
async def test_ai_memory_expires_after_ttl():
    clock = [100.0]
    requester = _fake_requester(
        (200, _success_payload("Première réponse."), {}),
        (200, _success_payload("Nouvelle réponse."), {}),
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        clock=lambda: clock[0],
        user_cooldown_seconds=0,
        memory_ttl_seconds=10,
        max_requests_per_window=5,
    )

    await ai.answer(7, "Première question")
    clock[0] += 11
    await ai.answer(7, "Question après expiration")

    second_messages = requester.await_args_list[1].args[0]["messages"]
    assert second_messages[0]["role"] == "system"
    assert second_messages[1:] == [
        {"role": "user", "content": "Question après expiration"}
    ]


@pytest.mark.asyncio
async def test_ai_enforces_per_user_cooldown_before_second_api_call():
    clock = [100.0]
    requester = _fake_requester((200, _success_payload("Réponse."), {}))
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
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
    assert requester.await_count == 1


@pytest.mark.asyncio
async def test_ai_enforces_rolling_request_quota():
    clock = [100.0]
    requester = _fake_requester((200, _success_payload("Réponse."), {}))
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
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
    assert requester.await_count == 1


@pytest.mark.asyncio
async def test_ai_unavailable_without_mistral_api_key():
    ai = MaitreDuJeuAI(api_key="")

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_ai_api_failure_returns_safe_error_status():
    requester = AsyncMock(side_effect=RuntimeError("boom"))
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.ERROR
    assert reply.text is None


@pytest.mark.asyncio
async def test_ai_mistral_rate_limit_maps_to_temporary_quota():
    requester = _fake_requester(
        (
            429,
            {"message": "rate limited"},
            {"x-request-id": "req-rate", "Retry-After": "12"},
        )
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.QUOTA
    assert reply.retry_after == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_ai_mistral_auth_rejection_is_unavailable():
    requester = _fake_requester(
        (401, {"message": "unauthorized"}, {"x-request-id": "req-auth"})
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_ai_accepts_structured_mistral_content_parts():
    requester = _fake_requester(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "Première partie."},
                                {"type": "text", "text": "Deuxième partie."},
                            ]
                        }
                    }
                ]
            },
            {},
        )
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        user_cooldown_seconds=0,
        max_requests_per_window=5,
    )

    reply = await ai.answer(7, "Question générale")

    assert reply.status is AIStatus.SUCCESS
    assert reply.text == "Première partie.\nDeuxième partie."


@pytest.mark.asyncio
async def test_ai_rejects_oversized_question_before_api_call():
    requester = _fake_requester(
        (200, _success_payload("Ne doit pas être appelée"), {})
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        question_max_chars=10,
        user_cooldown_seconds=0,
    )

    reply = await ai.answer(7, "Cette question est beaucoup trop longue")

    assert reply.status is AIStatus.TOO_LONG
    requester.assert_not_awaited()
