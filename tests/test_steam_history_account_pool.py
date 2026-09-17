import json


class _ProxyManager:
    def get_proxies_for_request(self, failed=False):
        return None

    def is_proxy_enabled(self):
        return False


def _prepare_client(monkeypatch, accounts):
    from app.services import steam_client

    steam_client._history_cache.clear()
    steam_client._history_pool_cooldowns.clear()
    steam_client._history_pool_cursor = 0
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
        return {"history": [["date", 1.0, "1"]], "currency": "CNY"}, 200

    monkeypatch.setattr(steam_client, "_fetch_history", fake_fetch)

    result = steam_client.SteamClient().fetch_history("item", return_currency=True)

    assert result["currency"] == "CNY"
    assert calls == ["account=one", "account=two"]
    assert "1" in steam_client._history_pool_cooldowns


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
            raise TypeError("legacy test double")
        calls.append(kwargs["cookies"])
        return [["date", 1.0, "1"]]

    monkeypatch.setattr(steam_client, "_fetch_history", legacy_fetch)

    result = steam_client.SteamClient().fetch_history("legacy-item")

    assert result == [["date", 1.0, "1"]]
    assert calls == ["primary-cookie"]


def test_empty_history_pool_preserves_anonymous_request_fallback(monkeypatch):
    steam_client = _prepare_client(monkeypatch, [])
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs["cookies"])
        return [["date", 1.0, "1"]], 200

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

    assert steam_client.SteamClient().fetch_history("first") is None
    assert steam_client.SteamClient().fetch_history("second") is None
    assert calls == [None]


def test_history_pool_excludes_unverified_or_different_currency_accounts(monkeypatch):
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

    assert [account["account_id"] for account in steam_client._history_accounts()] == ["one"]


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
        }
    ]
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
