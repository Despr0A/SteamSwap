import base64
import json
from types import SimpleNamespace

import pytest
import requests


def cookie(identity="1", *, expires=2000000, issued=1000000):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expires, "iat": issued}).encode()).decode().rstrip("=")
    return f"sessionid=session; steamLoginSecure={identity}%7C%7Cheader.{payload}.signature"


def test_selected_account_reads_its_history_cookie_without_changing_primary(monkeypatch):
    from app import config_loader
    primary = {"steam_id": "2", "cookies": cookie("2"), "session_id": "other"}
    monkeypatch.setattr(config_loader, "get_steam", lambda: primary)
    monkeypatch.setattr("app.accounts.get_current_account", lambda: {"id": "one", "steam_id": "1"})
    monkeypatch.setattr(config_loader, "get_steam_history_accounts", lambda: [{
        "account_id": "one", "steam_id": "1", "cookies": cookie("1"), "cookie_refreshed_at": 100,
    }])
    selected = config_loader.get_steam_credentials()
    assert selected["steam_id"] == "1"
    assert selected["session_id"] == "session"
    assert selected["cookie_refreshed_at"] == 100
    assert primary["steam_id"] == "2"


def test_account_selection_rejects_mislabelled_cookie(monkeypatch):
    from app import config_loader
    monkeypatch.setattr(config_loader, "get_steam", lambda: {"steam_id": "2", "cookies": cookie("2")})
    monkeypatch.setattr("app.accounts.get_current_account", lambda: {"id": "one", "steam_id": "1"})
    monkeypatch.setattr(config_loader, "get_steam_history_accounts", lambda: [{
        "account_id": "one", "steam_id": "1", "cookies": cookie("2"),
    }])
    assert config_loader.get_steam_credentials()["steam_id"] == "2"


@pytest.mark.parametrize("status", [429, 500, 302])
def test_cookie_validation_does_not_accept_failed_http_response(monkeypatch, status):
    from app.services import steam_auth
    reply = SimpleNamespace(status_code=status, url="https://steamcommunity.com/my/profile", json=lambda: {})
    class Session:
        cookies = {}
        headers = {}
        def get(self, *args, **kwargs): return reply
        def close(self): pass
    monkeypatch.setattr(steam_auth._req, "Session", Session)
    assert steam_auth._verify_steam_cookies_valid(cookie()) is False


def test_cookie_validation_network_failure_is_not_success(monkeypatch):
    from app.services import steam_auth
    class Session:
        cookies = {}
        headers = {}
        def get(self, *args, **kwargs): raise requests.ConnectionError("connection reset 10054")
        def close(self): pass
    monkeypatch.setattr(steam_auth._req, "Session", Session)
    assert steam_auth._verify_steam_cookies_valid(cookie()) is False


def test_history_refresh_account_persists_without_overwriting_active_cookie(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "_CREDENTIALS_FILE", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "_cache", {})
    config.update_steam_credentials(cookie("1"), "session", "1", "one")
    before = config.get_steam()["cookie_refreshed_at"]
    config.update_steam_credentials(cookie("2"), "session", "2", "two", activate=False)
    assert config.get_steam()["steam_id"] == "1"
    assert config.get_steam()["cookie_refreshed_at"] == before
    assert config.get_steam_history_accounts()[1]["cookie_refreshed_at"] > 0


@pytest.mark.parametrize("age, expiry, due", [
    (6*86400, 2000000, False), (7*86400, 2000000, True),
    (0, 999999, True), (0, 2000000, False),
])
def test_refresh_due_at_seven_days_or_earlier_token_expiry(age, expiry, due):
    from app.services.steam_auth import steam_cookie_refresh_reason
    creds = {"cookies": cookie(expires=expiry), "cookie_refreshed_at": 1000000-age}
    assert bool(steam_cookie_refresh_reason(creds, now=1000000)) is due


def test_missing_legacy_timestamp_uses_token_issue_time():
    from app.services.steam_auth import steam_cookie_refresh_reason
    assert steam_cookie_refresh_reason({"cookies": cookie(issued=300000)}, now=1000000) == "seven_days"


def test_periodic_refresh_is_independent_of_buff_opt_in_and_running_pipeline(monkeypatch):
    from app.services import workers, steam_auth, steam_refresh
    account = {"id": "one", "steam_id": "1", "username": "u", "password": "pw"}
    calls = []
    monkeypatch.setattr(steam_refresh, "_pending", {})
    monkeypatch.setattr(steam_refresh, "get_account", lambda aid: account)
    monkeypatch.setattr(steam_refresh, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(workers, "_session_keepalive_is_safe", lambda: False)
    monkeypatch.setattr(workers, "list_accounts", lambda: [account])
    monkeypatch.setattr("app.pipeline.is_shutdown_pending", lambda: False)
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda account: {"cookies": cookie(expires=1)})
    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", lambda **kwargs: calls.append(kwargs) or (True, "auto_ok", "ready"))
    workers._refresh_due_steam_cookies()
    assert calls == [{"force": True, "account_id": "one", "background": True}]


def test_expired_history_cookie_resumes_after_background_refresh(monkeypatch, tmp_path):
    from app.services import steam_client, steam_auth, steam_refresh
    from app import steam_history
    from tests.test_steam_history_account_pool import _prepare_client
    old = cookie(expires=1)
    new = cookie(expires=9999999999)
    accounts = [{"account_id": "one", "steam_id": "1", "cookies": old}]
    _prepare_client(monkeypatch, accounts)
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr("app.pipeline.is_shutdown_pending", lambda: False)
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda account: accounts[0])
    calls = []
    def relogin(**kwargs):
        assert kwargs == {"force": True, "account_id": "one", "background": True}
        calls.append("login")
        accounts[0]["cookies"] = new
        return True, "auto_ok", "ready"
    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", relogin)
    def fetch(*args, **kwargs):
        calls.append("history")
        assert kwargs["cookies"] == new
        return {"history": [["Oct 10 2026 01: +0", 1, "1"]], "currency": "CNY"}, 200
    monkeypatch.setattr(steam_client, "_fetch_history", fetch)
    client = steam_client.SteamClient()
    assert client.fetch_history("Item") is None
    assert calls == []
    steam_refresh.process_pending()
    assert client.fetch_history("Item")
    assert calls == ["login", "history"]


def test_pending_refresh_does_not_request_history_with_expired_cookie(monkeypatch, tmp_path):
    from app.services import steam_client, steam_auth
    from app import steam_history
    from tests.test_steam_history_account_pool import _prepare_client
    _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": cookie(expires=1)}])
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", lambda **kwargs: pytest.fail("synchronous login"))
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: pytest.fail("expired cookie sent"))
    client = steam_client.SteamClient()
    assert client.fetch_history("Item") is None
    assert client.get_last_history_error("Item")["code"] == "ACCOUNTS_REFRESH_PENDING"


def test_background_login_keeps_current_account_and_saves_target_identity(monkeypatch):
    from app.services import steam_auth
    writes = []
    monkeypatch.setattr(steam_auth, "get_account", lambda aid: {"id": aid, "steam_id": "2", "username": "u", "password": "p"})
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda account: {})
    monkeypatch.setattr(steam_auth, "set_current", lambda aid: pytest.fail("current account changed"))
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(steam_auth, "_do_steampy_login", lambda *args: (True, "", {"sessionid": "s", "steamLoginSecure": "2%7C%7Cnew"}))
    monkeypatch.setattr(steam_auth, "update_steam_creds", lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(steam_auth, "fetch_steam_profile_via_api", lambda *args: ("", ""))
    monkeypatch.setattr(steam_auth, "update_account", lambda *args, **kwargs: None)
    assert steam_auth._try_steam_auto_relogin_impl(force=True, account_id="two")[0]
    assert writes[0][0][2] == "2"
    assert writes[0][1] == {"account_id": "two"}


def test_login_rejects_wrong_identity_before_saving(monkeypatch):
    from app.services import steam_auth
    monkeypatch.setattr(steam_auth, "get_account", lambda aid: {"id": aid, "steam_id": "2", "username": "u", "password": "p"})
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda account: {})
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(steam_auth, "_do_steampy_login", lambda *args: (True, "", {"steamLoginSecure": "1%7C%7Cwrong"}))
    monkeypatch.setattr(steam_auth, "update_steam_creds", lambda *args, **kwargs: pytest.fail("wrong identity saved"))
    assert steam_auth._try_steam_auto_relogin_impl(force=True, account_id="two")[1] == "identity_mismatch"


def test_login_network_failure_keeps_endpoint_proxy_and_cause_without_secrets(monkeypatch):
    from steampy import client as steampy_client
    from app.services import steam_auth
    from app.state import get_log

    def failing_request(*args, **kwargs):
        raise requests.ConnectionError("connection reset 10054") from ConnectionResetError(10054, "reset")

    class Client:
        def __init__(self, **kwargs):
            self._session = requests.Session()
        def login(self):
            self._session.post("https://api.steampowered.com/auth?password=private", data={"password": "private"})

    monkeypatch.setattr(steampy_client, "SteamClient", Client)
    monkeypatch.setattr(requests.Session, "request", failing_request)
    result = steam_auth._do_steampy_login_once("u", "private", None, {"https": "http://u:private@proxy.example:8080"})
    assert result[0] is False
    diagnostic = get_log(0)[-1]["diagnostic"]
    assert diagnostic["code"] == "CONNECTION_RESET"
    assert diagnostic["endpoint"] == "api.steampowered.com/auth"
    assert diagnostic["proxy"] == "proxy.example:8080"
    assert diagnostic["method"] == "POST"
    assert diagnostic["stack"]
    assert any(cause.get("errno") == 10054 for cause in diagnostic["causes"])
    assert "private" not in json.dumps(diagnostic)
