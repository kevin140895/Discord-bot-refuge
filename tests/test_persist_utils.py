import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils import persist


def test_atomic_write(tmp_path):
    p = tmp_path / "data.json"
    persist.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


def test_read_json_fallback(tmp_path):
    p = tmp_path / "data.json"
    persist.atomic_write_json(p, {"a": 1})
    persist.atomic_write_json(p, {"a": 2})
    p.write_text("{")
    data = persist.read_json_safe(p)
    assert data == {"a": 1}

