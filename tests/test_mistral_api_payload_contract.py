from unittest.mock import AsyncMock

import pytest

from cogs.maitre_du_jeu_ai import AIStatus, MaitreDuJeuAI


@pytest.mark.asyncio
async def test_mistral_payload_keeps_server_ai_read_only():
    requester = AsyncMock(
        return_value=(
            200,
            {"choices": [{"message": {"content": "Réponse sûre."}}]},
            {},
        )
    )
    ai = MaitreDuJeuAI(
        requester=requester,
        api_key="",
        user_cooldown_seconds=0,
    )

    reply = await ai.answer(123, "Peux-tu m'aider ?")

    assert reply.status is AIStatus.SUCCESS
    payload = requester.await_args.args[0]
    assert payload["tool_choice"] == "none"
    assert "tools" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert "lecture seule" in payload["messages"][0]["content"]
