import json

from utils.persistence import read_json_safe
from utils.storage import load_json


def test_load_json_returns_list_default_when_file_is_missing(tmp_path):
    path = tmp_path / "missing.json"

    result = load_json(path, [])

    assert result == []
    assert isinstance(result, list)


def test_load_json_returns_default_when_primary_and_backup_are_corrupt(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not-json", encoding="utf-8")
    path.with_suffix(".json.bak").write_text("[also-not-json", encoding="utf-8")
    default = ["fallback"]

    result = load_json(path, default)

    assert result == ["fallback"]


def test_load_json_uses_valid_backup_before_default(tmp_path):
    path = tmp_path / "recover.json"
    path.write_text("{not-json", encoding="utf-8")
    path.with_suffix(".json.bak").write_text(
        json.dumps(["from-backup"]), encoding="utf-8"
    )

    result = load_json(path, ["fallback"])

    assert result == ["from-backup"]


def test_load_json_preserves_valid_empty_dict_instead_of_default(tmp_path):
    path = tmp_path / "empty-dict.json"
    path.write_text("{}", encoding="utf-8")

    result = load_json(path, [])

    assert result == {}


def test_read_json_safe_keeps_historical_empty_dict_fallback(tmp_path):
    path = tmp_path / "missing.json"

    result = read_json_safe(path)

    assert result == {}
