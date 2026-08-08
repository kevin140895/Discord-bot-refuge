from cogs.pari_xp import _build_result_description


def test_result_without_context_uses_only_main_message() -> None:
    description = _build_result_description(None, "Résultat principal")

    assert description == "Résultat principal"


def test_result_with_context_keeps_both_lines() -> None:
    description = _build_result_description("Contexte", "Résultat principal")

    assert description == "Contexte\nRésultat principal"


def test_empty_context_does_not_add_blank_line() -> None:
    description = _build_result_description("", "Résultat principal")

    assert description == "Résultat principal"
