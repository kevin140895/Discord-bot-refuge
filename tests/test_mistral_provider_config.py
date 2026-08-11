from pathlib import Path

import cogs.maitre_du_jeu_ai as mdj_ai


def test_mistral_provider_defaults_are_vibe_compatible():
    assert mdj_ai.AI_MODEL == "mistral-medium-3-5"
    assert mdj_ai.AI_API_URL == "https://api.mistral.ai/v1/chat/completions"


def test_openai_sdk_dependency_is_removed():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").casefold()
    assert "openai" not in requirements
