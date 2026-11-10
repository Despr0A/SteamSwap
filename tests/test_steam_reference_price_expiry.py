from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "fetched_at, ttl, requests_history",
    [
        (999.0, None, False),
        (700.001, None, False),
        (700.0, None, True),
        (699.0, None, True),
        (None, None, True),
        ("unknown", None, True),
        (0, None, True),
        (True, None, True),
        (1001.0, None, True),
        (float("nan"), None, True),
        (float("inf"), None, True),
        (950.0, 30, True),
        (650.0, 600, False),
    ],
)
def test_sell_reference_freshness_boundaries(monkeypatch, fetched_at, ttl, requests_history):
    from app import pipeline_steps as steps

    monkeypatch.setattr(steps.time, "time", lambda: 1000.0)
    pipeline = {} if ttl is None else {"steam_reference_price_ttl_seconds": ttl}
    assert steps._steam_reference_price_is_fresh({"fetched_at": fetched_at}, pipeline) is not requests_history


@pytest.mark.parametrize("history_enabled", [False, True])
def test_stale_reference_reads_history_before_pressure_and_reuses_it(monkeypatch, history_enabled):
    from app import pipeline_steps as steps

    calls = []
    enabled = {"buy.steam_sell_depth", "guard.max_discount"}
    if history_enabled:
        enabled.add("guard.history_data_window")
    monkeypatch.setattr(steps, "is_strategy_module_enabled", lambda cfg, side, module_id, default=True: module_id in enabled)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *a, **k: ({"smart_price": 100.0}, None))
    monkeypatch.setattr(steps, "_check_sell_pressure_precheck", lambda *a: calls.append("pressure") or True)
    monkeypatch.setattr(steps, "_check_reference_price_stability", lambda *a, **k: True)
    monkeypatch.setattr(steps, "_passes_custom_buy_modules", lambda *a, **k: True)
    monkeypatch.setattr(steps, "_passes_final_buff_precheck", lambda *a, **k: True)
    monkeypatch.setattr(steps, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *a: None)
    client = SimpleNamespace(fetch_history=lambda *a, **k: calls.append("history") or {
        "history": [["Oct 10 2026 01: +0", 100, "20"]], "currency": "CNY",
    })
    monkeypatch.setattr(steps, "SteamClient", lambda: client)
    selected, _ = steps.pick_stable_item(
        [{"goods_id": 1, "name": "Item", "min_price": 10}],
        {"pipeline": {"max_discount": 0.7}}, client,
        SimpleNamespace(analyze=lambda *a, **k: {"valid": False}), lambda: False,
    )
    assert (selected is None) is history_enabled
    assert calls == ["history", "pressure"]


def test_stale_reference_missing_history_rejects_before_other_checks(monkeypatch):
    from app import pipeline_steps as steps

    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *a, **k: ({"smart_price": 100.0}, None))
    monkeypatch.setattr(steps, "_check_sell_pressure_precheck", lambda *a: pytest.fail("history must be fetched first"))
    monkeypatch.setattr(steps, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *a: None)
    calls = []
    selected, failed = steps.pick_stable_item(
        [{"goods_id": 1, "name": "Item", "min_price": 80}],
        {"pipeline": {"max_discount": 0.7}},
        SimpleNamespace(fetch_history=lambda *a, **k: calls.append("history")),
        object(), lambda: False,
    )
    assert selected is None
    assert failed == {1}
    assert calls == ["history"]


@pytest.mark.parametrize("source", ["action", "ssr", "histogram", "unknown"])
def test_sell_reference_keeps_original_timestamp_through_memory_cache(monkeypatch, source):
    from app import pipeline_steps as steps
    from steam import market_orders
    from steam import session
    from app import config_loader

    now = [1000.0]
    calls = []
    monkeypatch.setattr(market_orders.time, "time", lambda: now[0])
    monkeypatch.setattr(market_orders, "_sell_orders_cache", {})
    orders = {"lowest_price": 100.0, "sell_orders": [(100.0, 10)]}
    monkeypatch.setattr(market_orders, "_fetch_action_orderbook_cny", lambda *a, **k: calls.append("action") or (orders if source == "action" else None, None))
    monkeypatch.setattr(market_orders, "_fetch_ssr_sell_orders_cny", lambda *a, **k: calls.append("ssr") or (orders if source == "ssr" else None, None))
    monkeypatch.setattr(market_orders, "db_get_item_nameid", lambda *a: "123")
    monkeypatch.setattr(market_orders, "jittered_sleep", lambda *a: None)
    monkeypatch.setattr(market_orders, "get_cached_item_orders_histogram", lambda *a, **k: {
        "data": {"success": 1, "lowest_sell_order": "10000", "sell_order_graph": [[100.0, 10]]},
        "age": 800.0, "fetched_at": 200.0 if source != "unknown" else None,
    })
    monkeypatch.setattr(config_loader, "get_steam_credentials", lambda: {"cookies": "cookie", "steam_id": "1"})
    monkeypatch.setattr(session, "create_market_session", lambda *a: object())
    monkeypatch.setattr(market_orders, "compute_smart_list_price", lambda *a, **k: (100.0, 10))

    first = steps._fetch_steam_sell_data("Item", {})
    expected_time = 1000.0 if source in {"action", "ssr"} else 200.0 if source == "histogram" else None
    assert first["fetched_at"] == expected_time
    assert steps._steam_reference_price_is_fresh(first, {}) is (source in {"action", "ssr"})
    request_count = len(calls)
    now[0] = 1010.0
    second = steps._fetch_steam_sell_data("Item", {})
    assert second["fetched_at"] == expected_time
    assert len(calls) == request_count


@pytest.mark.parametrize("circuit_open", [False, True])
def test_histogram_stale_fallback_preserves_timestamp(monkeypatch, circuit_open):
    from steam import market_orders

    monkeypatch.setattr(market_orders.time, "time", lambda: 100000.0)
    monkeypatch.setattr(market_orders, "_cb_open_until", 100100.0 if circuit_open else 0.0)
    monkeypatch.setattr(market_orders, "_cb_fail_streak", 0)
    monkeypatch.setattr(market_orders, "jittered_sleep", lambda *a: None)
    monkeypatch.setattr(market_orders, "get_proxy_manager", lambda: SimpleNamespace(get_proxies_for_request=lambda **k: None))
    monkeypatch.setattr(market_orders, "get_cached_item_orders_histogram", lambda *a, **k: {
        "data": {"success": 1}, "age": 99900.0, "fetched_at": 100.0,
    })
    session = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=500))
    result, error = market_orders.fetch_item_orders_histogram(session, "123", return_error=True)
    assert result["fetched_at"] == 100.0
    assert "缓存" in error


@pytest.mark.parametrize("value, expected", [(None, 300), (60, 60), (0, 1), (700000, 604800)])
def test_reference_price_ttl_config(value, expected):
    import warnings
    from app.config_schema import DEFAULTS, _validate_ranges, validate_and_fill

    override = {} if value is None else {"steam_reference_price_ttl_seconds": value}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = _validate_ranges(validate_and_fill({"pipeline": override}, DEFAULTS))
    assert cfg["pipeline"]["steam_reference_price_ttl_seconds"] == expected
