import json
import pytest


@pytest.fixture(autouse=True)
def isolated_history_database(monkeypatch, tmp_path):
    from app import steam_history

    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")


class _ProxyManager:
    def get_proxies_for_request(self, failed=False):
        return None

    def is_proxy_enabled(self):
        return False

    def get_next_proxy_dict(self, excluded=None):
        return None

    def should_always_use_proxy(self):
        return False


def _prepare_client(monkeypatch, accounts):
    from app.services import steam_client
    from app.services import steam_refresh

    monkeypatch.setattr(steam_refresh, "_pending", {})
    monkeypatch.setattr(steam_refresh, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(steam_refresh, "get_account", lambda aid: next((
        {"id": a["account_id"], "steam_id": a.get("steam_id"), "username": "u", "password": "p"}
        for a in accounts if a["account_id"] == aid
    ), None))

    steam_client._history_cache.clear()
    steam_client._history_pool_cooldowns.clear()
    steam_client._history_pool_cooldown_status.clear()
    steam_client._history_pool_cursor = 0
    monkeypatch.setattr("app.state.is_stop_requested", lambda: False)
    monkeypatch.setattr("utils.delay.jittered_sleep", lambda *args: None)
    monkeypatch.setattr(steam_client, "get_steam_history_credentials", lambda: accounts)
    monkeypatch.setattr(steam_client, "get_steam_credentials", lambda: {})
    monkeypatch.setattr(steam_client, "load_app_config_validated", lambda: {"steam": {}})
    monkeypatch.setattr(
        "app.accounts.get_current_account",
        lambda: {"id": "one", "currency_code": "CNY"},
    )
    monkeypatch.setattr(
        "app.accounts.list_accounts",
        lambda: [
            {"id": account["account_id"], "currency_code": "CNY", "region_check_ok": True}
            for account in accounts
        ],
    )
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: _ProxyManager(),
    )
    return steam_client


def test_history_429_cools_account_and_rotates_to_next(monkeypatch):
    steam_client = _prepare_client(
        monkeypatch,
        [
            {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
            {"account_id": "two", "steam_id": "2", "cookies": "account=two"},
        ],
    )
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs["cookies"])
        if kwargs["cookies"] == "account=one":
            return None, 429
        return {"history": [["Oct 10 2026 01: +0", 1.0, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(steam_client, "_fetch_history", fake_fetch)

    result = steam_client.SteamClient().fetch_history("item", return_currency=True)

    assert result["currency"] == "CNY"
    assert calls == ["account=one", "account=two"]
    assert "1" in steam_client._history_pool_cooldowns


def test_auth_refresh_429_queues_non_current_account_without_sync_login(monkeypatch):
    steam_client = _prepare_client(
        monkeypatch,
        [
            {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
            {"account_id": "two", "steam_id": "2", "cookies": "account=two"},
        ],
    )
    relogin_calls = []
    monkeypatch.setattr(steam_client, "_wait_for_history_accounts", lambda: False)
    monkeypatch.setattr(
        "app.services.steam_auth.try_steam_auto_relogin",
        lambda **kwargs: relogin_calls.append(True) or (True, "auto_ok", "ready"),
    )

    def fake_fetch(*args, **kwargs):
        if kwargs["cookies"] == "account=one":
            return None, 429
        return None, 429, True

    monkeypatch.setattr(steam_client, "_fetch_history", fake_fetch)

    assert steam_client.SteamClient().fetch_history("item") is None
    assert relogin_calls == []
    from app.services import steam_refresh
    assert steam_refresh._pending["two"]["reason"] == "auth_rejected"
    assert "2" not in steam_client._history_pool_cooldowns


def test_auth_refresh_429_queues_current_account_and_uses_other_account(monkeypatch):
    from app.services import steam_refresh
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
        {"account_id": "two", "steam_id": "2", "cookies": "account=two"},
    ])
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs["cookies"])
        if kwargs["cookies"] == "account=one":
            return None, 429, True
        return {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200, False
    monkeypatch.setattr("app.services.steam_auth.try_steam_auto_relogin", lambda **kw: pytest.fail("synchronous login"))
    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("item")
    assert calls == ["account=one", "account=two"]
    assert steam_refresh._pending["one"]["reason"] == "auth_rejected"
    assert steam_client._history_pool_cooldowns == {}


def test_observation_and_normal_pricing_use_the_same_history_request(monkeypatch):
    from app import pipeline_steps as steps

    client_module = _prepare_client(
        monkeypatch,
        [{"account_id": "one", "steam_id": "1", "cookies": "account=one"}],
    )
    calls = []

    def fake_fetch(name, **kwargs):
        calls.append((name, kwargs))
        return {"history": [["Oct 10 2026 01: +0", 100, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(client_module, "_fetch_history", fake_fetch)
    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_fetch_steam_sell_data", lambda *args, **kwargs: (
        {"smart_price": 100, "sell_orders": []}, None,
    ))
    monkeypatch.setattr(steps, "_check_reference_price_stability", lambda *args, **kwargs: True)
    config = {"_strategy_runtime": {"buy": {"enabled_modules": []}}}
    steps._adjust_ref_price_for_daily_high("Item", 100, config, None)
    [normal_request] = calls
    calls.clear()
    client_module._history_cache.clear()
    monkeypatch.setattr(client_module, "get_price_history", lambda *args, **kwargs: None)

    selected, _ = steps.pick_stable_item(
        [{"name": "Item", "goods_id": 1, "min_price": 10}], config,
        client_module.SteamClient(), object(), lambda: False,
        observation_only=True,
    )

    assert calls == [normal_request]
    assert normal_request[1]["cookies"] == "account=one"
    assert normal_request[1]["return_currency"] is True
    assert selected is None


def test_history_pool_keeps_primary_account_as_legacy_fallback(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [])
    monkeypatch.setattr(
        steam_client,
        "get_steam_credentials",
        lambda: {"steam_id": "primary", "cookies": "primary-cookie"},
    )
    calls = []

    def legacy_fetch(*args, **kwargs):
        if "return_status" in kwargs:
            raise TypeError("unexpected keyword argument 'return_status'")
        calls.append(kwargs["cookies"])
        return [["Oct 10 2026 01: +0", 1.0, "1"]]

    monkeypatch.setattr(steam_client, "_fetch_history", legacy_fetch)

    result = steam_client.SteamClient().fetch_history("legacy-item")

    assert result == [["Oct 10 2026 01: +0", 1.0, "1"]]
    assert calls == ["primary-cookie"]


def test_empty_history_pool_preserves_anonymous_request_fallback(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [])
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs["cookies"])
        return [["Oct 10 2026 01: +0", 1.0, "1"]], 200

    monkeypatch.setattr(steam_client, "_fetch_history", fake_fetch)

    assert steam_client.SteamClient().fetch_history("anonymous-item")
    assert calls == [None]


def test_anonymous_429_is_cooled_instead_of_retried_immediately(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [])
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs["cookies"])
        return None, 429

    monkeypatch.setattr(steam_client, "_fetch_history", fake_fetch)
    monkeypatch.setattr(steam_client, "_wait_for_history_accounts", lambda: False, raising=False)

    assert steam_client.SteamClient().fetch_history("first") is None
    assert steam_client.SteamClient().fetch_history("second") is None
    assert calls == [None]


def test_429_retries_each_proxy_before_cooling_account(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    from utils import proxy_manager

    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {
        "enabled": True, "strategy": 1,
        "proxies": [{"host": "p1", "port": 80}, {"host": "p2", "port": 80}],
    })
    manager = proxy_manager.ProxyManager()
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: manager)
    calls = []

    def fetch(name, **kwargs):
        calls.append((name, kwargs["cookies"], kwargs["proxies"]))
        assert "1" not in steam_client._history_pool_cooldowns
        if len(calls) < 3:
            return None, 429
        return {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("Item")
    assert len(calls) == 3
    assert calls[0][2] is None
    assert {call[2]["http"] for call in calls[1:]} == {"http://p1:80/", "http://p2:80/"}
    assert all(call[:2] == ("Item", "account=one") for call in calls)
    assert "1" not in steam_client._history_pool_cooldowns


def test_all_cooling_waits_on_same_item_then_recovers(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    clock = [100.0]
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: clock[0])
    steam_client._history_pool_cooldowns["1"] = 160.0
    logs, calls, sleeps = [], [], []
    monkeypatch.setattr("app.state.log", lambda msg, *args, **kwargs: logs.append(msg))

    def sleep(seconds):
        assert not calls
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(steam_client.time, "sleep", sleep)
    monkeypatch.setattr(steam_client, "_fetch_history", lambda name, **kwargs: (
        calls.append(name) or {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200,
    ))
    assert steam_client.SteamClient().fetch_history("Item")
    assert calls == ["Item"]
    assert sum(sleeps) == pytest.approx(60)
    assert any("全部账号均已进入429冷却" in msg and "60" in msg for msg in logs)
    assert any("30" in msg for msg in logs)


def test_429_exhausts_proxies_before_rotating_account(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
        {"account_id": "two", "steam_id": "2", "cookies": "account=two"},
    ])
    from utils import proxy_manager

    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {
        "enabled": True, "strategy": 1,
        "proxies": [{"host": "p1", "port": 80}, {"host": "p2", "port": 80}],
    })
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: manager)
    manager = proxy_manager.ProxyManager()
    clock = 100.0
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: clock)
    calls = []

    def fetch(name, **kwargs):
        calls.append(kwargs)
        if kwargs["cookies"] == "account=one":
            assert "1" not in steam_client._history_pool_cooldowns
            return None, 429 if kwargs["proxies"] is None else None
        assert steam_client._history_pool_cooldowns["1"] == 400
        return {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("Item")
    assert [call["cookies"] for call in calls] == ["account=one"] * 3 + ["account=two"]
    assert len({call["proxies"]["http"] for call in calls[1:3]}) == 2


def test_proxy_only_429_never_reuses_initial_proxy_or_falls_back_to_direct(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    from utils import proxy_manager

    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {
        "enabled": True, "strategy": 2,
        "proxies": [{"host": "p1", "port": 80}, {"host": "p2", "port": 80}],
    })
    manager = proxy_manager.ProxyManager()
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: manager)
    monkeypatch.setattr(steam_client, "_wait_for_history_accounts", lambda: False)
    calls = []

    def fetch(name, **kwargs):
        calls.append(kwargs["proxies"])
        return None, 429

    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("Item") is None
    assert {call["http"] for call in calls} == {"http://p1:80/", "http://p2:80/"}
    assert len(calls) == 2
    assert "1" in steam_client._history_pool_cooldowns


def test_empty_proxy_only_pool_makes_no_direct_request(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    from utils import proxy_manager

    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {
        "enabled": True, "strategy": 2, "proxies": [],
    })
    manager = proxy_manager.ProxyManager()
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: manager)
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: pytest.fail("No direct fallback"))
    assert steam_client.SteamClient().fetch_history("Item") is None


def test_429_waits_after_exhaustion_and_resumes_current_item(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    clock, calls, sleeps = [100.0], [], []
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def fetch(name, **kwargs):
        calls.append((name, clock[0]))
        if len(calls) == 1:
            return None, 429
        return {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(steam_client.time, "sleep", sleep)
    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("Item")
    assert calls == [("Item", 100), ("Item", 400)]
    assert sum(sleeps) == pytest.approx(300)


def test_cooldown_wait_responds_to_stop_without_network(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    clock, stopped = [100.0], [False]
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: clock[0])
    steam_client._history_pool_cooldowns["1"] = 400
    monkeypatch.setattr("app.state.is_stop_requested", lambda: stopped[0])
    monkeypatch.setattr(steam_client.time, "sleep", lambda seconds: stopped.__setitem__(0, True))
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: pytest.fail("No request while cooling"))
    assert steam_client.SteamClient().fetch_history("Item") is None


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_quarantines_account_until_refresh(monkeypatch, status):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(steam_client, "_wait_for_history_accounts", lambda: False)
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: (None, status))
    assert steam_client.SteamClient().fetch_history("Item") is None
    from app.services import steam_refresh
    assert steam_refresh._pending["one"]["reason"] == "auth_rejected"
    assert steam_client._select_history_accounts() == []
    assert steam_client._history_pool_cooldown_status == {}


def test_non_429_failure_preserves_two_attempts_and_static_proxy(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [
        {"account_id": "one", "steam_id": "1", "cookies": "account=one"},
    ])
    monkeypatch.setattr(steam_client, "load_app_config_validated", lambda: {"steam": {"proxy": "http://static:80"}})
    calls = []

    def fetch(name, **kwargs):
        calls.append(kwargs["proxies"])
        return None, 500

    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    assert steam_client.SteamClient().fetch_history("Item") is None
    assert calls == [{"http": "http://static:80", "https": "http://static:80"}] * 2
    assert not steam_client._history_pool_cooldowns


def test_history_pool_includes_accounts_without_wallet_currency_metadata(monkeypatch):
    steam_client = _prepare_client(
        monkeypatch,
        [
            {"account_id": "one", "steam_id": "1", "cookies": "current"},
            {"account_id": "two", "steam_id": "2", "cookies": "different"},
            {"account_id": "three", "steam_id": "3", "cookies": "unverified"},
        ],
    )
    monkeypatch.setattr(
        "app.accounts.list_accounts",
        lambda: [
            {"id": "one", "currency_code": "CNY", "region_check_ok": True},
            {"id": "two", "currency_code": "USD", "region_check_ok": True},
            {"id": "three", "currency_code": "CNY", "region_check_ok": False},
        ],
    )

    assert [account["account_id"] for account in steam_client._history_accounts()] == ["one", "two", "three"]


def test_history_credentials_are_filtered_and_upserted(tmp_path, monkeypatch):
    import config

    credentials_path = tmp_path / "credentials.json"
    monkeypatch.setattr(config, "_CREDENTIALS_FILE", credentials_path)
    monkeypatch.setattr(config, "_cache", {})

    config.update_steam_credentials(
        "sessionid=first; secret=drop; steamLoginSecure=1%7C%7Cone",
        "first",
        "1",
        "acc-1",
    )
    config.update_steam_credentials(
        "sessionid=second; steamLoginSecure=1%7C%7Ctwo",
        "second",
        "1",
        "acc-1",
    )

    saved = json.loads(credentials_path.read_text(encoding="utf-8"))
    accounts = config.get_steam_history_accounts()
    assert len(saved["steam"]["history_accounts"]) == 1
    assert accounts == [
        {
            "account_id": "acc-1",
            "steam_id": "1",
            "cookies": "sessionid=second; steamLoginSecure=1%7C%7Ctwo",
            "cookie_refreshed_at": saved["steam"]["cookie_refreshed_at"],
        }
    ]
    assert accounts[0]["cookie_refreshed_at"] > 0
    assert "secret=" not in credentials_path.read_text(encoding="utf-8")

    config.remove_steam_history_account("acc-1", "1")
    assert config.get_steam_history_accounts() == []


def test_low_level_history_can_return_429_status(monkeypatch):
    from steam import client

    class Response:
        status_code = 429

    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: Response())

    result, status = client.fetch_history(
        "item",
        cookies="steamLoginSecure=x",
        return_status=True,
    )

    assert result is None
    assert status == 429


def test_history_credentials_follow_account_when_steam_id_changes(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "_CREDENTIALS_FILE", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "_cache", {})
    config.update_steam_credentials("steamLoginSecure=1%7C%7Cone", "s1", "1", "acc-1")
    config.update_steam_credentials("steamLoginSecure=2%7C%7Ctwo", "s2", "2", "acc-1")

    accounts = config.get_steam_history_accounts()
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "acc-1"
    assert accounts[0]["steam_id"] == "2"
