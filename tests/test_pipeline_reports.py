from types import SimpleNamespace

import pytest

from app import pipeline_steps as steps


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.setattr(steps, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *a, **k: None)
    monkeypatch.setattr(steps, "_check_reference_price_stability", lambda *a, **k: True)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *a, **k: ({
        "smart_price": 100, "sell_orders": [(100, 10)], "fetched_at": steps.time.time(),
    }, None))
    return {"name": "Item", "goods_id": 1, "min_price": 10}


def test_unstable_candidate_reports_reason_without_crashing(candidate):
    reports = []
    selected, failed = steps.pick_stable_item(
        [candidate], {"pipeline": {"max_discount": 0.7}},
        SimpleNamespace(fetch_history=lambda *a, **k: {"history": [["date", 100, "1"]]}),
        SimpleNamespace(analyze=lambda *a, **k: {
            "valid": True, "is_stable": False, "status": "VOLATILE", "msg": "CV exceeds limit",
        }),
        lambda: False, item_report_fn=reports.append,
    )
    assert selected is None
    assert failed == {1}
    assert len(reports) == 1
    assert reports[0]["reason"] == "stability_analysis"
    assert reports[0]["checks"][-1]["analysis_status"] == "VOLATILE"


def test_price_report_has_formula_ttl_and_linked_network_result(candidate):
    candidate["min_price"] = 80
    reports, network, logs = [], [], []
    steps.pick_stable_item(
        [candidate], {"pipeline": {"max_discount": 0.7}},
        SimpleNamespace(fetch_history=lambda *a, **k: {"history": [["date", 100, "1"]]}),
        object(), lambda: False, item_report_fn=reports.append,
        network_report_fn=network.append, log_fn=lambda *a: logs.append(a),
    )
    assert len(reports) == 1
    report = reports[0]
    assert report["decision"] == "reject"
    assert report["reason"] == "max_discount"
    assert report["report_id"] == network[0]["report_id"]
    freshness = next(c for c in report["checks"] if c["check"] == "reference_freshness")
    assert freshness["ttl_seconds"] == 300
    assert 0 <= freshness["age_seconds"] < 300
    price = next(c for c in report["checks"] if c["check"] == "reference_price")
    assert price["fee_factor"] == 1.15
    assert price["comparison"] == "<"
    assert price["estimated_ratio"] == pytest.approx(0.92)
    assert logs == []  # Item details are contained in one report.


def test_report_survives_state_export_restore_with_redaction(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    from app.state import State
    from app.pipeline_context import PipelineContext
    from app.routes import status

    state = State()
    PipelineContext(state, "flow-1").item_report({
        "summary": "Item rejected", "decision": "reject", "reason": "max_discount",
        "checks": [{"cookies": "private", "detail": "password=private"}],
    })
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "get_log", state.get_log)
    monkeypatch.setattr(status, "load_app_config_validated", lambda: {})
    response = status.api_log_export()
    content = Path(response["path"]).read_text(encoding="utf-8")
    assert '"layer": "item"' in content
    assert "private" not in content
    restored = State()
    restored.replace_log(state.get_log())
    assert restored.get_log()[0]["report"]["decision"] == "reject"
    assert "private" not in json.dumps(restored.get_log())


@pytest.mark.parametrize("age", [0, 43199, 43200, 50000, None])
@pytest.mark.parametrize("cost", [10, 80])
def test_history_cache_or_refresh_precedes_every_price_check(monkeypatch, tmp_path, candidate, age, cost):
    from app import steam_history
    from app.services import steam_client

    now = 100000.0
    monkeypatch.setattr(steam_client.time, "time", lambda: now)
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(steam_client, "_history_cache", {})
    old = {"history": [["Oct 10 2026 01: +0", 100, "20"]], "currency": "CNY"}
    new = {"history": [["Oct 10 2026 02: +0", 101, "30"]], "currency": "CNY"}
    if age is not None:
        steam_history.merge_price_history("Item", old, fetched_at=now-age)
    remote_calls = []
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *a: remote_calls.append(True) or new)
    received = []
    def adjust(*args, history_data, **kwargs):
        received.append(history_data["history"])
        return 100
    monkeypatch.setattr(steps, "_adjust_ref_price_for_daily_high", adjust)
    client = steam_client.SteamClient()
    reports, network = [], []
    candidate["min_price"] = cost
    selected, _ = steps.pick_stable_item(
        [candidate], {"pipeline": {"max_discount": 0.7}}, client,
        SimpleNamespace(analyze=lambda history, **k: received.append(history) or {"valid": True, "is_stable": True}),
        lambda: False, item_report_fn=reports.append, network_report_fn=network.append,
    )
    refreshed = age is None or age >= 43200
    assert len(remote_calls) == int(refreshed)
    assert received[0][-1] == (new if refreshed else old)["history"][-1]
    assert (selected is not None) == (cost == 10)
    if cost == 10:
        assert received[1] is received[0]  # Pricing and analysis share one snapshot.
    history_report = network[0]
    assert history_report["source"] == ("refresh" if refreshed else "database_cache")
    saved = steam_history.get_price_history("Item", now=now)
    assert saved["history"] == received[0]
    assert saved["fetched_at"] == (now if refreshed else now-age)
    client.fetch_history("Item")
    assert len(remote_calls) == int(refreshed)
    assert client.get_last_history_report("Item")["source"] == "memory_cache"
    assert client.get_last_history_report("Item")["network_attempted"] is False
    steam_client._history_cache.clear()
    client.fetch_history("Item")
    assert client.get_last_history_report("Item")["source"] == "database_cache"
    assert len(remote_calls) == int(refreshed)


def test_checkout_does_not_use_old_reference_when_history_is_unavailable(monkeypatch):
    monkeypatch.setattr(steps, "SteamClient", lambda: SimpleNamespace(fetch_history=lambda *a, **k: None))
    buyer = SimpleNamespace(get_sell_orders=lambda *a: [{"id": "one", "price": "10"}])
    item = {"name": "Item", "goods_id": 1, "min_price": 10,
            "_steam_sell_data": {"smart_price": 100, "sell_orders": [(100, 10)]}}
    result = steps.lock_and_confirm_payment(
        buyer, item, {"pipeline": {"max_discount": 0.7}}, 100, 0,
        lambda *a: None, lambda *a: False, lambda *a: None, lambda: False, lambda *a: None,
    )
    assert result is steps.SKIP_VERIFICATION_FAILED
