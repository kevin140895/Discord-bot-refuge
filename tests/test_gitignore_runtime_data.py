from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / ".gitignore"


def _rules() -> list[str]:
    return [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_runtime_data_is_not_broadly_reincluded() -> None:
    rules = _rules()

    assert "data/*" in rules
    assert "data/**/*.json" in rules
    assert "!data/pari_xp/" not in rules
    assert "!data/pari_xp/*.json" not in rules
    assert "!data/economy/*.json" not in rules


def test_only_static_shop_catalog_is_reincluded() -> None:
    rules = _rules()

    assert "!data/economy/" in rules
    assert "data/economy/*" in rules
    assert "!data/economy/shop.json" in rules
