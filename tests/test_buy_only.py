import json
from pathlib import Path

import pytest

from app import strategy_engine as se
from app.config_schema import DEFAULTS, validate_and_fill


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "STRATEGY_STORE_PATH", tmp_path / "strategies.json")
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    monkeypatch.setattr(se, "load_app_config_validated", lambda: DEFAULTS)


def test_strategy_catalog_only_exposes_buy_without_erasing_old_records():
    saved = {"strategies": [{"id": "custom.sell.old", "strategy_type": "sell"}]}
    se.STRATEGY_STORE_PATH.write_text(json.dumps(saved), encoding="utf-8")
    se.USER_MODULE_STORE_PATH.write_text(json.dumps({"modules": [
        {"id": "custom.sell.old", "strategy_types": ["sell"]},
    ]}), encoding="utf-8")
    payload = se.get_strategy_payload()
    assert set(payload["active"]) == {"buy"}
    assert set(payload["limits"]) == {"buy"}
    assert all(s["strategy_type"] == "buy" for s in payload["strategies"])
    assert all(m["strategy_types"] == ["buy"] for m in payload["modules"])
    assert json.loads(se.STRATEGY_STORE_PATH.read_text(encoding="utf-8")) == saved
    assert se.get_strategy("custom.sell.old") is None


def test_sell_strategy_cannot_be_imported_or_executed():
    old = {"name": "old", "strategy_type": "sell", "steps": [
        {"module_id": "action.steam_list", "enabled": True, "params": {}},
    ]}
    with pytest.raises(se.StrategyError):
        se.import_strategy(old)
    with pytest.raises(se.StrategyError):
        se.apply_strategy_to_config({}, "sell", strategy_override=old)


def test_old_sell_config_is_ignored_but_buy_market_parameters_survive():
    cfg = validate_and_fill({"pipeline": {
        "sell_strategy": 1, "listing_delay_seconds": 1,
        "listing_check_interval_seconds": 1, "max_listings_per_item": 3,
        "steam_listings_debug": True, "sell_trend_days": 7,
        "sell_price_wall_volume": 30, "sell_price_max_ignore_volume": 2,
        "sell_pressure_threshold": 1.5,
    }, "steam_confirm": {"enabled": True},
        "strategies": {"active_sell_strategy_id": "system.sell.immediate"}})
    assert "steam_confirm" not in cfg
    assert set(cfg["strategies"]) == {"active_buy_strategy_id"}
    for key in ("sell_strategy", "listing_delay_seconds", "listing_check_interval_seconds",
                "max_listings_per_item", "steam_listings_debug", "sell_trend_days"):
        assert key not in cfg["pipeline"]
    assert cfg["pipeline"]["sell_price_wall_volume"] == 30
    assert cfg["pipeline"]["sell_price_max_ignore_volume"] == 2
    assert cfg["pipeline"]["sell_pressure_threshold"] == 1.5
    assert se.simulate_strategy({"strategy_id": "system.buy.default"})["ok"]


def test_no_listing_writer_or_worker_is_shipped():
    root = Path(__file__).resolve().parent.parent
    for name in ("app/sell_pipeline.py", "app/steam_listings.py", "app/steam_confirm.py", "steam/market.py"):
        assert not (root / name).exists()
    assert "listing_check_worker" not in (root / "app/api.py").read_text(encoding="utf-8")
    html = (root / "web/index.html").read_text(encoding="utf-8")
    assert 'data-strategy-view="sell"' not in html
    assert "cfg-steam-auto-confirm" not in html
    assert "wiz-identity-secret" not in html
