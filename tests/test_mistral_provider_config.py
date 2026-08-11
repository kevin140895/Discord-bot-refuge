from pathlib import Path


def test_mistral_provider_defaults_are_vibe_compatible():
    source = Path("cogs/maitre_du_jeu_ai.py").read_text(encoding="utf-8")
    assert 'MISTRAL_MODEL", "mistral-medium-3-5"' in source
    assert "https://api.mistral.ai/v1/chat/completions" in source
    assert "MISTRAL_API_KEY" in source
    assert "OPENAI_" not in source


def test_openai_sdk_dependency_is_removed():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").casefold()
    assert "openai" not in requirements
