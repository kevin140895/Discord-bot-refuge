import logging
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import config
from utils.api_meter import APIMeter


def _kinds(specs):
    return [spec[2] for spec in specs]


def test_isolated_429_at_low_usage_is_rate_limit_warning_not_hard(monkeypatch):
    monkeypatch.setattr(config, "API_SOFT_LIMIT_PCT", 85.0)
    monkeypatch.setattr(config, "API_HARD_LIMIT_PCT", 95.0)
    meter = APIMeter()

    specs = meter._summary_alert_specs(total=70, too_many=1, usage_pct=0.7)

    assert _kinds(specs) == ["rate_limit"]
    level, message, key, notify = specs[0]
    assert level == logging.WARNING
    assert message == "api.rate_limit usage=1% calls=70 429=1"
    assert key == "rate_limit"
    assert notify is True
    assert "hard_limit" not in message


def test_soft_budget_threshold_emits_only_soft_warning(monkeypatch):
    monkeypatch.setattr(config, "API_SOFT_LIMIT_PCT", 85.0)
    monkeypatch.setattr(config, "API_HARD_LIMIT_PCT", 95.0)
    meter = APIMeter()

    specs = meter._summary_alert_specs(total=8500, too_many=0, usage_pct=85.0)

    assert _kinds(specs) == ["soft"]
    level, message, key, notify = specs[0]
    assert level == logging.WARNING
    assert message == "api.soft_limit usage=85% calls=8500 429=0"
    assert key == "soft"
    assert notify is False


def test_hard_budget_threshold_emits_hard_without_duplicate_soft(monkeypatch):
    monkeypatch.setattr(config, "API_SOFT_LIMIT_PCT", 85.0)
    monkeypatch.setattr(config, "API_HARD_LIMIT_PCT", 95.0)
    meter = APIMeter()

    specs = meter._summary_alert_specs(total=9500, too_many=0, usage_pct=95.0)

    assert _kinds(specs) == ["hard"]
    level, message, key, notify = specs[0]
    assert level == logging.ERROR
    assert message == "api.hard_limit usage=95% calls=9500 429=0"
    assert key == "hard"
    assert notify is True


def test_429_and_hard_budget_keep_two_distinct_alerts(monkeypatch):
    monkeypatch.setattr(config, "API_SOFT_LIMIT_PCT", 85.0)
    monkeypatch.setattr(config, "API_HARD_LIMIT_PCT", 95.0)
    meter = APIMeter()

    specs = meter._summary_alert_specs(total=9700, too_many=2, usage_pct=97.0)

    assert _kinds(specs) == ["rate_limit", "hard"]
    assert specs[0][0] == logging.WARNING
    assert specs[1][0] == logging.ERROR
    assert specs[0][3] is True
    assert specs[1][3] is True
