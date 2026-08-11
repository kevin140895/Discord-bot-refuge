from pathlib import Path

import bot


def test_support_modules_are_not_treated_as_discord_extensions():
    cogs_dir = Path(__file__).resolve().parents[1] / "cogs"
    discovered_python_modules = {
        path.stem for path in cogs_dir.glob("*.py") if path.name != "__init__.py"
    }

    assert "maitre_du_jeu_ai" in discovered_python_modules
    assert "maitre_du_jeu_ai" in bot.COG_SUPPORT_MODULES
