from storage.xp_store import xp_store
from utils import xp_adapter


def test_missing_cached_balance_is_zero_after_sqlite_bootstrap(monkeypatch):
    cached_data = {"1": {"xp": 500, "level": 2}}
    monkeypatch.setattr(xp_store, "data", cached_data)

    assert xp_adapter.get_balance(2) == 0
    assert xp_store.data == {"1": {"xp": 500, "level": 2}}


def test_cached_balance_is_authoritative(monkeypatch):
    monkeypatch.setattr(xp_store, "data", {"7": {"xp": 750, "level": 2}})

    assert xp_adapter.get_balance(7) == 750
