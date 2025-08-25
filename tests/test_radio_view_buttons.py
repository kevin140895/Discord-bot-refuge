from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys
import pytest

# Permet d'importer "view.RadioView" depuis la racine du projet
sys.path.append(str(Path(__file__).resolve().parents[1]))

from view import RadioView


@pytest.mark.asyncio
async def test_radio_view_has_expected_buttons():
    view = RadioView()
    expected = {
        "radio_rapfr": "Rap FR",
        "radio_rap": "Rap US",
        "radio_rock": "Rock",
        "radio_hiphop": "Radio Hip-Hop",
    }

    for custom_id, label in expected.items():
        button = next(
            (child for child in view.children if getattr(child, "custom_id", None) == custom_id),
            None,
        )
        assert button is not None, f"Button with custom_id '{custom_id}' not found"
        assert getattr(button, "label", None) == label, (
            f"Label for '{custom_id}' should be '{label}', got '{getattr(button, 'label', None)}'"
        )


@pytest.mark.asyncio
async def test_radio_view_buttons_call_methods():
    view = RadioView()
    mapping = {
        "radio_rapfr": "radio_rapfr",
        "radio_rap": "radio_rap",
        "radio_rock": "radio_rock",
        "radio_hiphop": "radio_hiphop",
    }

    for custom_id, cmd_name in mapping.items():
        button = next(
            (child for child in view.children if getattr(child, "custom_id", None) == custom_id),
            None,
        )
        assert button is not None, f"Button with custom_id '{custom_id}' not found"

        mock = AsyncMock()
        cog = SimpleNamespace(**{cmd_name: mock})

        # Interaction minimale pour le callback : .client.get_cog(...) doit renvoyer notre "cog"
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=lambda name, c=cog: c)
        )

        # Exécute le callback du bouton
        await button.callback(interaction)

        # Vérifie que la méthode du cog a bien été appelée
        mock.assert_awaited_once()