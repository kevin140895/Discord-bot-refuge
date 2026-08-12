from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")

# Helpers used in production modules to read one environment key. The value is
# the positional argument containing the environment-variable name.
_ENV_READER_KEY_INDEX = {
    "_read_int": 2,
    "_read_float": 2,
    "_read_bool": 2,
    "_read_string": 2,
    "_read_positive_int_env": 0,
    "_env_int": 0,
    "_env_float": 0,
    "_env_positive_int": 0,
    "_env_nonnegative_int": 0,
    "_env_non_negative_int": 0,
    "_parse_thresholds_env": 0,
    "_parse_int_tuple_env": 0,
    "_parse_str_tuple_env": 0,
    "_csv_ints": 0,
}

SECRET_KEYS = {
    "DISCORD_TOKEN",
    "MISTRAL_API_KEY",
    "YOUTUBE_COOKIES_B64",
    "NHL_ODDS_API_KEY",
}


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    for raw_line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            continue
        if key in values:
            duplicates.add(key)
        values[key] = value.strip()
    assert not duplicates, f"Variables dupliquées dans .env.example: {sorted(duplicates)}"
    return values


def _string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _resolve_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _environment_keys_in_python(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = _string_constants(tree)
    keys: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        key: str | None = None

        if name == "getenv" and node.args:
            key = _resolve_string(node.args[0], constants)
        elif name in _ENV_READER_KEY_INDEX:
            index = _ENV_READER_KEY_INDEX[name]
            if len(node.args) > index:
                key = _resolve_string(node.args[index], constants)
        elif (
            name == "get"
            and node.args
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"env", "environ", "source"}
        ):
            key = _resolve_string(node.args[0], constants)

        if key and _ENV_KEY_RE.fullmatch(key):
            keys.add(key)

    return keys


def _production_environment_keys() -> set[str]:
    keys: set[str] = set()
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] == "tests":
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        keys.update(_environment_keys_in_python(path))
    return keys


def _getenv_default(path: Path, key: str) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "getenv":
            continue
        if len(node.args) < 2:
            continue
        found_key = _resolve_string(node.args[0], _string_constants(tree))
        if found_key != key:
            continue
        default = _resolve_string(node.args[1], _string_constants(tree))
        if default is not None:
            return default
    return None


def test_env_example_documents_every_production_environment_variable() -> None:
    documented = set(_env_example_values())
    used = _production_environment_keys()

    missing = used - documented
    stale = documented - used

    assert not missing, f"Variables utilisées mais absentes de .env.example: {sorted(missing)}"
    assert not stale, f"Variables documentées mais non utilisées: {sorted(stale)}"


def test_env_example_contains_no_real_secret_values() -> None:
    values = _env_example_values()
    for key in SECRET_KEYS:
        assert key in values, f"Secret non documenté: {key}"
        assert values[key] == "", f"{key} doit rester vide dans .env.example"


def test_voice_checkpoint_example_matches_production_default() -> None:
    values = _env_example_values()
    production_default = _getenv_default(
        ROOT / "utils" / "persistence.py",
        "VOICE_CP_DEBOUNCE_SECONDS",
    )

    assert production_default == "300"
    assert values["VOICE_CP_DEBOUNCE_SECONDS"] == production_default
