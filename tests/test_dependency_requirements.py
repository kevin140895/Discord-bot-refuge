from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> list[str]:
    return [
        line.strip()
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_runtime_requirements_exclude_test_only_dependencies():
    runtime = _requirements("requirements.txt")

    assert not any(line.lower().startswith("pytest") for line in runtime)
    assert not any(line.lower().startswith("freezegun") for line in runtime)


def test_dev_requirements_extend_runtime_and_include_test_tools():
    dev = _requirements("requirements-dev.txt")

    assert "-r requirements.txt" in dev
    assert "pytest" in dev
    assert "pytest-asyncio" in dev
    assert "freezegun" in dev
