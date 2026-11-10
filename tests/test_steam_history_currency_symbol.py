import pytest

from tests.test_error_diagnostics import response


def test_history_defaults_bare_yen_symbol_to_cny(monkeypatch):
    from steam import client

    payload = {"success": True, "prices": [["Oct 10 2026 01: +0", 10, "1"]], "price_prefix": "\u00a5", "price_suffix": ""}
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: response(payload))
    data = client.fetch_history("Item", return_currency=True)
    assert data["currency"] == "CNY"
    assert "currency_symbol" not in data


@pytest.mark.parametrize("currency", ["CNY", "JPY"])
def test_symbol_uses_verified_currency_of_request_account(monkeypatch, currency):
    from app.services import steam_client
    from tests.test_steam_cookie_refresh import cookie

    account = {"account_id": "two", "steam_id": "2", "cookies": cookie("2")}
    monkeypatch.setattr("app.accounts.get_account", lambda aid: {"id": "two", "steam_id": "2", "currency_code": currency, "region_check_ok": True})
    monkeypatch.setattr("app.accounts.get_current_account", lambda: {"id": "one", "steam_id": "1", "currency_code": "CNY"})
    monkeypatch.setattr("app.services.account_region.refresh_account_region_currency", lambda *args, **kwargs: pytest.fail("verified currency refreshed"))
    result = {"history": [], "currency": None, "currency_symbol": "\u00a5"}
    assert steam_client._resolve_history_currency(result, account)["currency"] == currency


def test_unverified_currency_uses_saved_currency_without_wallet_refresh(monkeypatch):
    from app.services import steam_client
    from tests.test_steam_cookie_refresh import cookie

    account = {"account_id": "two", "steam_id": "2", "cookies": cookie("2")}
    monkeypatch.setattr("app.accounts.get_account", lambda aid: {"id": "two", "steam_id": "2", "currency_code": "JPY", "region_check_ok": False})
    monkeypatch.setattr("app.services.account_region.refresh_account_region_currency", lambda *args, **kwargs: pytest.fail("history currency must not query the wallet"))
    result = {"history": [], "currency": None, "currency_symbol": "\u00a5"}
    assert steam_client._resolve_history_currency(result, account)["currency"] == "JPY"


def test_missing_currency_defaults_yen_symbol_to_cny_without_wallet_refresh(monkeypatch):
    from app.services import steam_client
    from tests.test_steam_cookie_refresh import cookie

    account = {"account_id": "two", "steam_id": "2", "cookies": cookie("2")}
    monkeypatch.setattr("app.accounts.get_account", lambda aid: {"id": "two", "steam_id": "2", "currency_code": "", "region_check_ok": False})
    monkeypatch.setattr("app.services.account_region.refresh_account_region_currency", lambda *args, **kwargs: pytest.fail("history currency must not query the wallet"))
    result = {"history": [], "currency": None, "currency_symbol": "\u00a5"}
    assert steam_client._resolve_history_currency(result, account)["currency"] == "CNY"


def test_missing_currency_without_symbol_uses_history_account_identity(monkeypatch):
    from app.services import steam_client
    from tests.test_steam_cookie_refresh import cookie

    account = {"account_id": "pool-only", "steam_id": "2", "cookies": cookie("2")}
    monkeypatch.setattr("app.accounts.get_account", lambda aid: None)
    monkeypatch.setattr("app.accounts.get_current_account", lambda: None)
    result = {"history": [], "currency": None}

    assert steam_client._resolve_history_currency(result, account)["currency"] == "CNY"


def test_mismatched_cookie_cannot_supply_currency(monkeypatch):
    from app.services import steam_client
    from tests.test_steam_cookie_refresh import cookie

    account = {"account_id": "two", "steam_id": "2", "cookies": cookie("1")}
    monkeypatch.setattr("app.accounts.get_account", lambda aid: {"id": "two", "steam_id": "2", "currency_code": "CNY", "region_check_ok": True})
    monkeypatch.setattr("app.services.account_region.refresh_account_region_currency", lambda *args, **kwargs: pytest.fail("wrong identity used"))
    result = {"history": [], "currency": None, "currency_symbol": "\u00a5"}
    assert steam_client._resolve_history_currency(result, account)["currency"] is None


def test_yen_history_reaches_cny_storage_through_service(monkeypatch, tmp_path):
    from app.services import steam_client
    from app import steam_history
    from tests.test_steam_cookie_refresh import cookie
    from tests.test_steam_history_account_pool import _prepare_client

    _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": cookie(expires=9999999999)}])
    monkeypatch.setattr("app.accounts.get_account", lambda aid: {"id": "one", "steam_id": "1", "currency_code": "CNY", "region_check_ok": True})
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: ({"history": [["Oct 10 2026 01: +0", 10, "1"]], "currency": None, "currency_symbol": "\u00a5"}, 200))
    client = steam_client.SteamClient()
    result = client.fetch_history("Item", return_currency=True)
    assert result["currency"] == "CNY"
    assert result["history"][0][1] == 10
    assert client.get_last_history_error("Item") is None
