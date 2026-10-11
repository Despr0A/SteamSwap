from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("volume, stable", [(20, True), (0, False)])
def test_stability_counts_volume_and_falls_back_to_points(volume, stable):
    from analysis.stability import analyze_by_time

    now = datetime.now(timezone.utc)
    history = [
        [(now - timedelta(days=i)).strftime("%b %d %Y %H: +0"), 100, str(volume)]
        for i in range(20)
    ]
    result = analyze_by_time(history, currency="CNY", min_daily_trades=5)
    assert result["valid"] is True
    assert result["is_stable"] is stable


def test_history_headers_overrides_and_auth_redirect_metadata(monkeypatch):
    from steam import client

    calls = []
    response = SimpleNamespace(status_code=429, history=[SimpleNamespace(headers={
        "Location": "https://login.steampowered.com/jwt/refresh?token=private",
    })])
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: calls.append(kwargs) or response)
    assert client.fetch_history("Item", return_metadata=True) == (None, 429, True)
    assert calls[0]["headers"]["User-Agent"] == client.HISTORY_USER_AGENT
    assert calls[0]["headers"]["Accept"].startswith("application/json")
    assert "Accept-Language" not in calls[0]["headers"]
    assert client.fetch_history("Item", return_status=True, headers={
        "User-Agent": "Custom", "Accept-Language": "zh-CN",
    }) == (None, 429)
    assert calls[1]["headers"]["User-Agent"] == "Custom"
    assert calls[1]["headers"]["Accept-Language"] == "zh-CN"


def test_history_failure_diagnostics_exclude_credentials(monkeypatch):
    from steam import client

    logs = []
    response = SimpleNamespace(
        status_code=429, history=[],
        url="https://login.steampowered.com/jwt/refresh?token=private",
        headers={"Retry-After": "300"},
    )
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: response)
    assert client.fetch_history("Item", return_metadata=True, log_fn=logs.append) == (None, 429, True)
    assert "login.steampowered.com/jwt/refresh" in logs[0]
    assert "retry_after=300" in logs[0]
    assert "private" not in logs[0]

    def fail(*args, **kwargs):
        raise client.requests.exceptions.ProxyError("http://user:private@proxy/")

    monkeypatch.setattr(client.requests, "get", fail)
    assert client.fetch_history("Item", return_metadata=True, log_fn=logs.append) == (None, None, False)
    assert "exception=ProxyError" in logs[-1]
    assert "private" not in logs[-1]


@pytest.mark.parametrize("with_logs", [True, False])
def test_observation_skips_sell_depth_when_history_is_missing(monkeypatch, with_logs):
    from app import pipeline_steps as steps

    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *args, **kwargs: pytest.fail("history unavailable"))
    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *args: None)
    selected, failed = steps.pick_stable_item(
        [{"goods_id": 1, "name": "Item", "min_price": 10}], {},
        SimpleNamespace(fetch_history=lambda *args, **kwargs: None),
        SimpleNamespace(), lambda: False,
        log_fn=(lambda *args: None) if with_logs else None,
        observation_only=True,
    )
    assert selected is None
    assert 1 in failed


def test_keepalive_refreshes_steam_and_buff(monkeypatch):
    from app.services import buff_auth, steam_auth, workers

    calls = []
    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", lambda: calls.append("steam") or (True, "ok", "ready"))
    monkeypatch.setattr(buff_auth, "try_buff_auto_relogin", lambda: calls.append("buff") or (True, "ok", "ready"))
    monkeypatch.setattr(workers, "log", lambda *args, **kwargs: None)
    workers._keepalive_sessions()
    assert calls == ["steam", "buff"]


def test_forced_relogin_does_not_reuse_existing_cookie(monkeypatch):
    from app.services import steam_auth

    calls = []
    monkeypatch.setattr(steam_auth, "get_current_account", lambda: {
        "id": "one", "steam_id": "1", "username": "user", "password": "pw",
    })
    monkeypatch.setattr(steam_auth, "set_current", lambda *args: None)
    monkeypatch.setattr(steam_auth, "get_steam_credentials", lambda: {
        "steam_id": "1", "cookies": "steamLoginSecure=old",
    })
    monkeypatch.setattr(steam_auth, "_verify_steam_cookies_valid", lambda *args: pytest.fail("must refresh"))
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(steam_auth, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(steam_auth, "_do_steampy_login", lambda *args: calls.append(True) or (False, "captcha", {}))
    assert steam_auth._try_steam_auto_relogin_impl(force=True)[0] is False
    assert calls == [True]


@pytest.mark.parametrize("ratio, observation", [(0.8, False), (0.7, False), (0.68, False), (0.8, True)])
def test_early_discount_skips_history_without_changing_observation(monkeypatch, ratio, observation):
    from app import pipeline_steps as steps

    calls = []
    enabled = {"buy.steam_sell_depth", "guard.max_discount", "guard.history_data_window"}
    monkeypatch.setattr(steps, "is_strategy_module_enabled", lambda config, side, module_id, default=True: module_id in enabled)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *args, **kwargs: ({"smart_price": 100}, None))
    monkeypatch.setattr(steps, "_check_sell_pressure_precheck", lambda *args: True)
    monkeypatch.setattr(steps, "_check_reference_price_stability", lambda *args, **kwargs: True)
    monkeypatch.setattr(steps, "_adjust_ref_price_for_daily_high", lambda *args, **kwargs: calls.append("adjust") or 100)
    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *args: None)

    def fetch(*args, **kwargs):
        calls.append("history")
        return {"history": [["Oct 10 2026 01: +0", 100, "20"]], "currency": "CNY"}

    item = {"goods_id": 1, "name": "Item", "min_price": ratio * 100 / steps.STEAM_FEE_FACTOR}
    selected, failed = steps.pick_stable_item(
        [item], {"pipeline": {"max_discount": 0.7}},
        SimpleNamespace(fetch_history=fetch),
        SimpleNamespace(analyze=lambda *args, **kwargs: {"valid": False}),
        lambda: False, observation_only=observation,
    )
    assert selected is None
    if observation:
        assert calls == ["history", "adjust"]
    elif ratio >= 0.7:
        assert calls == []
        assert 1 in failed
    else:
        assert calls == ["adjust", "history"]
