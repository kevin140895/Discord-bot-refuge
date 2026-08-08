import json

from storage.xp_store import xp_store
from utils import xp_adapter


def test_disk_balance_fallback_does_not_overwrite_newer_cached_xp(
    tmp_path, monkeypatch
):
    path = tmp_path / "xp.json"
    path.write_text(
        json.dumps(
            {
                "1": {"xp": 100, "level": 1},
                "2": {"xp": 200, "level": 1},
            }
        ),
        encoding="utf-8",
    )

    cached_data = {"1": {"xp": 500, "level": 2}}
    monkeypatch.setattr(xp_store, "path", path)
    monkeypatch.setattr(xp_store, "data", cached_data)

    assert xp_adapter.get_balance(2) == 200

    # Reading user 2 from disk must not merge the whole stale disk snapshot
    # into the authoritative in-memory cache and roll user 1 back to 100 XP.
    assert xp_store.data == {"1": {"xp": 500, "level": 2}}
    assert "2" not in xp_store.data


def test_cached_balance_is_authoritative_without_disk_read(monkeypatch):
    monkeypatch.setattr(xp_store, "data", {"7": {"xp": 750, "level": 2}})

    def unexpected_disk_read(_path):
        raise AssertionError("cached balance must not read the disk")

    monkeypatch.setattr(xp_adapter, "read_json_safe", unexpected_disk_read)

    assert xp_adapter.get_balance(7) == 750
