import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests


def response(payload=None, *, status=200, url=None, content_type="application/json"):
    def decode():
        if isinstance(payload, Exception):
            raise payload
        return payload
    return SimpleNamespace(
        status_code=status, history=[],
        url=url or "https://steamcommunity.com/market/pricehistory/?token=private",
        headers={"Content-Type": content_type, "Retry-After": "300"},
        text="", content=b"", json=decode,
    )


@pytest.mark.parametrize("payload, code, stage", [
    (ValueError("invalid JSON token=private"), "INVALID_JSON", "decode"),
    ([], "INVALID_RESPONSE", "validate"),
    ({"success": False, "error": "access denied"}, "API_REJECTED", "validate"),
    ({"success": True}, "INVALID_RESPONSE", "validate"),
    ({"success": True, "prices": {}}, "INVALID_RESPONSE", "validate"),
    ({"success": True, "prices": []}, "EMPTY_HISTORY", "validate"),
])
def test_history_preserves_response_status_and_classifies_failure(monkeypatch, payload, code, stage):
    from steam import client
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: response(payload))
    data, status, diagnostic = client.fetch_history("Item", return_diagnostics=True)
    assert data is None
    assert status == 200
    assert diagnostic["code"] == code
    assert diagnostic["stage"] == stage
    assert diagnostic["endpoint"] == "steamcommunity.com/market/pricehistory/"
    assert "private" not in json.dumps(diagnostic)


@pytest.mark.parametrize("exc, code", [
    (requests.exceptions.ProxyError("http://user:private@proxy:8080/?token=private"), "PROXY_ERROR"),
    (requests.exceptions.ConnectTimeout("connect failed"), "CONNECT_TIMEOUT"),
    (requests.exceptions.ReadTimeout("read failed"), "READ_TIMEOUT"),
    (requests.exceptions.SSLError("certificate verify failed"), "TLS_ERROR"),
    (requests.exceptions.ConnectionError(ConnectionResetError(10054, "reset")), "CONNECTION_RESET"),
])
def test_history_exception_has_safe_cause_and_source(monkeypatch, exc, code):
    from steam import client
    def fail(*args, **kwargs):
        raise exc
    monkeypatch.setattr(client.requests, "get", fail)
    data, status, diagnostic = client.fetch_history("Item", return_diagnostics=True)
    assert data is None and status is None
    assert diagnostic["code"] == code
    assert diagnostic["exception"] == type(exc).__name__
    assert diagnostic["source"].endswith(":fail")
    assert "private" not in json.dumps(diagnostic)
    if code == "CONNECTION_RESET":
        assert "10054" in json.dumps(diagnostic)


def test_refresh_429_retains_redirect_chain_without_query(monkeypatch):
    from steam import client
    reply = response(status=429, url="https://login.steampowered.com/jwt/refresh?redir=private")
    reply.history = [SimpleNamespace(status_code=302,
        url="https://steamcommunity.com/market/pricehistory/?token=private",
        headers={"Location": reply.url})]
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: reply)
    _, status, diagnostic = client.fetch_history("Item", return_diagnostics=True)
    assert status == 429
    assert diagnostic["code"] == "AUTH_REFRESH_RATE_LIMITED"
    assert diagnostic["retry_after"] == "300"
    assert diagnostic["redirects"][0]["status"] == 302
    assert "private" not in json.dumps(diagnostic)


def test_history_429_after_completed_refresh_is_not_refresh_endpoint_throttling(monkeypatch):
    from steam import client
    reply = response(status=429)
    reply.history = [SimpleNamespace(status_code=302,
        url="https://login.steampowered.com/jwt/refresh?token=private",
        headers={"Location": "https://steamcommunity.com/market/pricehistory/?token=private"})]
    monkeypatch.setattr(client.requests, "get", lambda *args, **kwargs: reply)
    _, _, diagnostic = client.fetch_history("Item", return_diagnostics=True)
    assert diagnostic["code"] == "RATE_LIMITED"
    assert diagnostic["endpoint"] == "steamcommunity.com/market/pricehistory/"
    assert "private" not in json.dumps(diagnostic)


def test_service_and_pipeline_preserve_request_context(monkeypatch, tmp_path):
    from app import pipeline_steps as steps, steam_history
    from app.services import steam_client as service
    from tests.test_steam_history_account_pool import _prepare_client
    _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": "account=one"}])
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr("steam.client.requests.get", lambda *args, **kwargs: response(ValueError("bad JSON")))
    monkeypatch.setattr(steps, "set_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "jittered_sleep", lambda *args: None)
    logs = []
    client = service.SteamClient()
    steps.pick_stable_item(
        [{"goods_id": 1, "name": "Item", "min_price": 10}], {}, client,
        SimpleNamespace(), lambda: False, log_fn=lambda msg, level: logs.append(msg),
        observation_only=True,
    )
    error = client.get_last_history_error("Item")
    assert error["account"] == "one"
    assert error["code"] == "INVALID_JSON"
    assert error["attempt"] == 2
    assert error["failure_counts"]["INVALID_JSON"] == 2
    assert error["request_id"]
    history_report = client.get_last_history_report("Item")
    assert history_report["request_id"] == error["request_id"]
    assert history_report["network_attempted"] is True
    assert history_report["attempts"] == 2
    assert history_report["diagnostic"]["code"] == "INVALID_JSON"
    assert any(error["request_id"] in msg and "INVALID_JSON" in msg for msg in logs)
    monkeypatch.setattr(service, "_fetch_history", lambda *args, **kwargs: (
        {"history": [["Oct 10 2026 01: +0", 10, "1"]], "currency": "CNY"}, 200))
    assert client.fetch_history("Next")
    assert client.get_last_history_error("Item") is None


def test_diagnostics_are_isolated_between_threads():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.services.steam_client import SteamClient
    client = SteamClient()
    barrier = Barrier(2)
    def record(name):
        client._request_context.error = {"item": name, "app_id": 730}
        barrier.wait(timeout=3)
        return client.get_last_history_error(name)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(record, ["First", "Second"]))
    assert [row["item"] for row in results] == ["First", "Second"]


def test_state_redacts_message_diagnostic_and_restored_logs():
    from app.state import State
    state = State()
    state.log("proxy=http://user:private@host:8080/ token=private steamLoginSecure=private", "warn",
              diagnostic={"code": "TEST", "cookies": "private", "detail": "password=private"})
    exported = state.get_log()
    assert exported[0]["diagnostic"]["code"] == "TEST"
    assert "private" not in json.dumps(exported)
    state.replace_log([{"msg": "sessionid=private", "diagnostic": {"token": "private"}}])
    assert "private" not in json.dumps(state.get_log())


def test_storage_failure_has_distinct_stage_and_exception(monkeypatch, tmp_path):
    from app import steam_history
    from app.services import steam_client as service
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    service._history_cache.clear()
    monkeypatch.setattr(service.SteamClient, "_fetch_history_impl", lambda *args: {
        "history": [["Oct 10 2026 01: +0", 10, "1"]], "currency": "CNY"})
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(service, "merge_price_history", fail)
    client = service.SteamClient()
    assert client.fetch_history("Item") is None
    error = client.get_last_history_error("Item")
    assert error["code"] == "HISTORY_STORAGE_ERROR"
    assert error["stage"] == "persist"
    assert error["exception"] == "OperationalError"


@pytest.mark.parametrize("payload, code", [
    ({"history": [["Oct 10 2026 01: +0", 10, "1"]], "currency": "UNKNOWN"}, "HISTORY_CURRENCY_UNSUPPORTED"),
    ({"history": [["bad date", 10, "1"]], "currency": "CNY"}, "INVALID_HISTORY_POINTS"),
])
def test_normalization_failure_is_distinct(monkeypatch, tmp_path, payload, code):
    from app import steam_history
    from app.services import steam_client as service
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    service._history_cache.clear()
    monkeypatch.setattr(service.SteamClient, "_fetch_history_impl", lambda *args: payload)
    client = service.SteamClient()
    assert client.fetch_history("Item") is None
    assert client.get_last_history_error("Item")["code"] == code


def test_adapter_internal_typeerror_does_not_trigger_duplicate_request(monkeypatch, tmp_path):
    from app import steam_history
    from app.services import steam_client as service
    from tests.test_steam_history_account_pool import _prepare_client
    _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": "account=one"}])
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise TypeError("internal decode bug")
    monkeypatch.setattr(service, "_fetch_history", fail)
    client = service.SteamClient()
    assert client.fetch_history("Item") is None
    assert calls == [True]
    assert client.get_last_history_error("Item")["code"] == "HISTORY_ADAPTER_ERROR"


def test_export_keeps_safe_structured_diagnostic(monkeypatch, tmp_path):
    from app.routes import status
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(status, "get_log", lambda *args: [{
        "msg": "token=private", "category": "steam", "flow_id": "flow-one",
        "diagnostic": {"code": "CONNECTION_RESET", "causes": [{"winerror": 10054}], "cookies": "private"},
    }])
    result = status.api_log_export()
    content = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "CONNECTION_RESET" in content and "10054" in content and "flow-one" in content
    assert "private" not in content


def test_buff_unknown_write_keeps_exception_and_safe_cause(monkeypatch):
    from buff.buyer import BuffBuyer
    from buff.request_policy import BuffWriteResultUnknown
    from tests.test_buff_request_policy import FakeSession, no_wait_policy
    session = FakeSession(requests.exceptions.ConnectionError("token=private connection reset 10054"))
    buyer = BuffBuyer("session=ready; csrf_token=csrf", session=session, request_policy=no_wait_policy())
    logs = []
    monkeypatch.setattr("app.state.log", lambda *args, **kwargs: logs.append(kwargs.get("diagnostic")))
    with pytest.raises(BuffWriteResultUnknown) as caught:
        buyer._make_request("POST", "https://buff.163.com/api/market/buy?token=private")
    error = caught.value.diagnostic
    assert error["code"] == "WRITE_RESULT_UNKNOWN"
    assert error["method"] == "POST"
    assert error["request_attempted"] is True
    assert error["causes"][1]["type"] == "ConnectionError"
    assert "10054" in json.dumps(error)
    assert "private" not in json.dumps(error)
    assert len(session.calls) == 1
    assert logs == [error]


def test_cookie_expiry_hint_contains_no_credential_values():
    import base64
    from app.services.steam_client import _credential_diagnostics
    payload = base64.urlsafe_b64encode(b'{"exp":1}').decode().rstrip("=")
    result = _credential_diagnostics({"steam_id": "1", "cookies": f"steamLoginSecure=2%7C%7Cheader.{payload}.signature"})
    assert result == {"cookie_present": True, "cookie_identity_matches": False, "cookie_expired": True}


@pytest.mark.parametrize("text", [
    "Authorization: Bearer private", "Cookie: steamLoginSecure=private; sessionid=private",
    '{"password": "private", "access_token": "private"}',
    "proxy=http://user:private@proxy:8080/ request=https://steamcommunity.com/market/?jwt=private",
])
def test_redaction_does_not_leak_common_secret_formats(text):
    from utils.diagnostics import redact_text
    assert "private" not in redact_text(text)


def test_summary_preserves_false_flags_and_explicit_no_response():
    from utils.diagnostics import one_line
    text = one_line({"code": "CONNECTION_RESET", "status": None, "cookie_expired": False, "auth_refresh_redirected": False})
    assert "status=no_response" in text
    assert "cookie_expired=False" in text
    assert "auth_refresh_redirected=False" in text
