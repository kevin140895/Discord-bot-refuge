from __future__ import annotations

import configparser
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
MYPY_CONFIG = ROOT / "mypy.ini"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def test_production_runtime_and_mypy_target_python_311() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    python_stages = set(re.findall(r"^FROM python:(\d+\.\d+)-slim", dockerfile, re.MULTILINE))
    assert python_stages == {"3.11"}

    config = configparser.ConfigParser()
    config.read(MYPY_CONFIG, encoding="utf-8")
    assert config.get("mypy", "python_version") == "3.11"


def test_ci_covers_production_and_next_migration_runtime_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'name: docker-runtime (Python 3.11 production)' in workflow
    assert '- python-version: "3.11"\n            target: production' in workflow
    assert '- python-version: "3.12"\n            target: migration' in workflow
    assert 'name: mypy-core (Python 3.11 production)' in workflow
    assert 'python-version: "3.13"' not in workflow
    assert 'python-version: "3.14"' not in workflow


def test_ci_verifies_the_built_image_python_minor() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "assert sys.version_info[:2] == (3, 11), sys.version" in workflow
