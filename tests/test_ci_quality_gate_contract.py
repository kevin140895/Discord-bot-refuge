from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
CODEQL_WORKFLOW = ROOT / ".github" / "workflows" / "codeql.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"


def test_python_quality_gate_runs_all_required_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m ruff check ." in workflow
    assert "python -m mypy main.py bot.py config.py" in workflow
    assert "python -m compileall -q main.py bot.py config.py cogs utils" in workflow
    assert 'python -c "import bot, config, main; print(\'core imports ok\')"' in workflow
    assert "python -m pip_audit -r requirements.txt" in workflow


def test_development_requirements_include_quality_tools() -> None:
    requirements = DEV_REQUIREMENTS.read_text(encoding="utf-8").splitlines()

    assert "mypy" in requirements
    assert "ruff" in requirements
    assert "pip-audit" in requirements


def test_dependabot_covers_python_actions_and_docker_dependencies() -> None:
    dependabot = DEPENDABOT.read_text(encoding="utf-8")

    assert 'package-ecosystem: "pip"' in dependabot
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'package-ecosystem: "docker"' in dependabot


def test_codeql_scans_python_with_current_major_action() -> None:
    workflow = CODEQL_WORKFLOW.read_text(encoding="utf-8")

    assert "github/codeql-action/init@v4" in workflow
    assert "github/codeql-action/analyze@v4" in workflow
    assert "languages: python" in workflow
    assert "queries: security-extended" in workflow
