import importlib
import os
import time


def test_config_loads_env_vars(monkeypatch):
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("GUILD_ID", "123456")
    monkeypatch.setenv("TZ", "UTC")
    import config
    importlib.reload(config)
    assert config.GUILD_ID == 123456
    assert config.TZ == "UTC"
    assert os.environ["TZ"] == "UTC"

    # POSIX exposes time.tzset(), so changing TZ also updates time.tzname.
    # Windows has no tzset(); config intentionally keeps the env value only.
    if hasattr(time, "tzset"):
        assert time.tzname[0] == "UTC"

    # Restore previous timezone for subsequent tests.
    if original_tz is not None:
        os.environ["TZ"] = original_tz
    else:
        os.environ.pop("TZ", None)
    if hasattr(time, "tzset"):
        time.tzset()
