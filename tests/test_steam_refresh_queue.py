import pytest

from tests.test_steam_cookie_refresh import cookie


@pytest.fixture(autouse=True)
def refresh_state(monkeypatch):
    from app.services import steam_refresh
    monkeypatch.setattr(steam_refresh, "_pending", {})
    monkeypatch.setattr(steam_refresh, "load_app_config_validated", lambda: {})
    monkeypatch.setattr("app.pipeline.is_shutdown_pending", lambda: False)


def test_running_pipeline_refreshes_non_current_account(monkeypatch):
    from app.services import workers, steam_auth, steam_refresh

    account = {"id": "two", "steam_id": "2", "username": "u", "password": "p"}
    calls = []
    monkeypatch.setattr(steam_refresh, "get_account", lambda aid: account)
    monkeypatch.setattr(workers, "_session_keepalive_is_safe", lambda: False)
    monkeypatch.setattr(workers, "list_accounts", lambda: [account])
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda a: {"cookies": cookie("2", expires=1)})
    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", lambda **kw: calls.append(kw) or (True, "auto_ok", "ready"))
    workers._refresh_due_steam_cookies()
    assert calls and calls[0]["account_id"] == "two"


def test_expired_account_is_excluded_before_request_without_repeated_warning(monkeypatch):
    from tests.test_steam_history_account_pool import _prepare_client
    from app import state

    client = _prepare_client(monkeypatch, [
        {"account_id": "two", "steam_id": "2", "cookies": cookie("2", expires=1)},
        {"account_id": "one", "steam_id": "1", "cookies": cookie(expires=9999999999)},
    ])
    logs = []
    monkeypatch.setattr(state, "log", lambda message, level="info", **kw: logs.append((message, level)))
    for _ in range(3):
        assert [a["account_id"] for a in client._select_history_accounts()] == ["one"]
    assert not [message for message, level in logs if level in ("warn", "error")]


@pytest.fixture
def queued_account(monkeypatch):
    from app.services import steam_refresh, steam_auth
    target = {"id": "one", "steam_id": "1", "username": "u", "password": "p"}
    credentials = {"cookies": cookie(expires=1)}
    now = [100.0]
    logs = []
    monkeypatch.setattr(steam_refresh, "get_account", lambda aid: target)
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda a: credentials)
    monkeypatch.setattr(steam_refresh.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(steam_refresh, "log", lambda message, level="info", **kw: logs.append((message, level)))
    steam_refresh.request_refresh({**target, **credentials}, "expired")
    return steam_refresh, steam_auth, target, credentials, now, logs


def test_network_retry_backs_off_and_requests_do_not_reset_delay(monkeypatch, queued_account):
    queue, auth, target, credentials, now, logs = queued_account
    calls = []
    monkeypatch.setattr(auth, "try_steam_auto_relogin", lambda **kw: calls.append(kw) or (False, "network_error", "timeout"))
    queue.process_pending()
    for _ in range(10):
        queue.request_refresh({**target, **credentials}, "expired")
        queue.process_pending()
    assert len(calls) == 1
    now[0] += 60
    queue.process_pending()
    assert len(calls) == 2
    now[0] += 119
    queue.process_pending()
    assert len(calls) == 2
    now[0] += 1
    queue.process_pending()
    assert len(calls) == 3


@pytest.mark.parametrize("status", ["no_creds", "wrong_creds", "need_2fa", "captcha", "identity_mismatch"])
def test_manual_failure_waits_for_credential_edit(monkeypatch, queued_account, status):
    queue, auth, target, credentials, now, logs = queued_account
    calls = []
    monkeypatch.setattr(auth, "try_steam_auto_relogin", lambda **kw: calls.append(kw) or (False, status, "manual"))
    queue.process_pending()
    now[0] += 86400
    queue.request_refresh({**target, **credentials}, "expired")
    queue.process_pending()
    assert len(calls) == 1
    assert len([line for line, level in logs if level == "warn"]) == 1
    target["password"] = "updated"
    queue.process_pending()
    assert len(calls) == 2


def test_manual_login_clears_quarantine_without_another_login(monkeypatch, queued_account):
    queue, auth, target, credentials, now, logs = queued_account
    monkeypatch.setattr(auth, "try_steam_auto_relogin", lambda **kw: pytest.fail("manual cookie overwritten"))
    credentials.update(cookies=cookie(expires=9999999999), cookie_refreshed_at=auth.time.time())
    queue.process_pending()
    assert not queue.is_history_account_blocked({**target, **credentials})


def test_duplicate_requests_during_login_do_not_start_another_login(monkeypatch, queued_account):
    import threading
    queue, auth, target, credentials, now, logs = queued_account
    started, release = threading.Event(), threading.Event()
    calls = []
    def login(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(5)
        return True, "auto_ok", "ready"
    monkeypatch.setattr(auth, "try_steam_auto_relogin", login)
    thread = threading.Thread(target=queue.process_pending)
    thread.start()
    try:
        assert started.wait(5)
        for _ in range(5):
            queue.request_refresh({**target, **credentials}, "expired")
            queue.process_pending()
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_all_expired_accounts_return_without_waiting_or_warning(monkeypatch):
    from tests.test_steam_history_account_pool import _prepare_client
    from app import state
    client = _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": cookie(expires=1)}])
    logs = []
    monkeypatch.setattr(state, "log", lambda message, level="info", **kw: logs.append(level))
    monkeypatch.setattr(client, "_wait_for_history_accounts", lambda: pytest.fail("global wait"))
    monkeypatch.setattr(client, "_fetch_history", lambda *a, **kw: pytest.fail("expired request"))
    instance = client.SteamClient()
    for name in ("A", "B", "C"):
        assert instance.fetch_history(name) is None
        assert instance.get_last_history_error(name)["code"] == "ACCOUNTS_REFRESH_PENDING"
    assert "warn" not in logs


@pytest.mark.parametrize("current, blocked", [("one", True), ("two", False)])
def test_checkout_defers_only_its_own_account(monkeypatch, current, blocked):
    from app.services import steam_auth
    monkeypatch.setattr(steam_auth, "get_current_account", lambda: {"id": current})
    monkeypatch.setattr("app.services.buff_checkout_guard.get_unresolved_checkout", lambda: {"status": "pending"})
    monkeypatch.setattr("app.state.get_status", lambda: {"status": "running"})
    assert steam_auth._background_refresh_blocked("one") is blocked


def test_running_pipeline_without_checkout_allows_current_account_refresh(monkeypatch):
    from app.services import steam_auth
    monkeypatch.setattr(steam_auth, "get_current_account", lambda: {"id": "one"})
    monkeypatch.setattr("app.services.buff_checkout_guard.get_unresolved_checkout", lambda: None)
    monkeypatch.setattr("app.state.get_status", lambda: {"status": "running"})
    assert not steam_auth._background_refresh_blocked("one")


@pytest.mark.parametrize("change", ["checkout", "password", "cookies", "deleted"])
def test_background_result_does_not_overwrite_changed_credentials(monkeypatch, change):
    from contextlib import nullcontext
    from app.services import steam_auth
    original = {"id": "one", "steam_id": "1", "username": "u", "password": "p"}
    current = dict(original)
    credentials = {"cookies": "old"}
    monkeypatch.setattr("app.services.buff_auth.get_buff_auth_lock", nullcontext)
    monkeypatch.setattr("app.services.buff_checkout_guard.buff_activity_guard", nullcontext)
    monkeypatch.setattr(steam_auth, "get_account", lambda aid: current)
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda a: credentials)
    monkeypatch.setattr(steam_auth, "_background_refresh_blocked", lambda aid: change == "checkout")
    monkeypatch.setattr(steam_auth, "update_steam_creds", lambda *a, **kw: pytest.fail("stale credentials saved"))
    if change == "password":
        current["password"] = "new"
    elif change == "cookies":
        credentials["cookies"] = "manual"
    elif change == "deleted":
        current.clear()
    result = steam_auth._save_background_credentials(original, {"cookies": "old"}, "new", "session", "1")
    assert result[0] is False
    assert result[1] == ("deferred" if change == "checkout" else "superseded")


def test_one_manual_failure_does_not_block_another_account(monkeypatch, queued_account):
    queue, auth, target, credentials, now, logs = queued_account
    second = {**target, "id": "two", "steam_id": "2"}
    monkeypatch.setattr(queue, "get_account", lambda aid: target if aid == "one" else second)
    queue.request_refresh({**second, **credentials}, "expired")
    calls = []
    def login(**kwargs):
        calls.append(kwargs["account_id"])
        if kwargs["account_id"] == "one":
            return False, "need_2fa", "manual"
        return True, "auto_ok", "ready"
    monkeypatch.setattr(auth, "try_steam_auto_relogin", login)
    queue.process_pending()
    assert calls == ["one", "two"]
    assert queue.is_history_account_blocked({**target, **credentials})
    assert not queue.is_history_account_blocked({**second, **credentials})


def test_background_login_saves_only_target_without_profile_writes(monkeypatch):
    from contextlib import nullcontext
    from app.services import steam_auth
    account = {"id": "two", "steam_id": "2", "username": "u", "password": "p"}
    writes = []
    monkeypatch.setattr(steam_auth, "get_account", lambda aid: account)
    monkeypatch.setattr(steam_auth, "get_steam_credentials_for_account", lambda a: {"cookies": "old"})
    monkeypatch.setattr(steam_auth, "_background_refresh_blocked", lambda aid: False)
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(steam_auth, "_do_steampy_login", lambda *args: (True, "", {"sessionid": "s", "steamLoginSecure": "2%7C%7Cnew"}))
    monkeypatch.setattr(steam_auth, "update_steam_creds", lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(steam_auth, "set_current", lambda *a: pytest.fail("changed current account"))
    monkeypatch.setattr(steam_auth, "update_account", lambda *a, **kw: pytest.fail("concurrent profile write"))
    monkeypatch.setattr(steam_auth, "fetch_steam_profile_via_api", lambda *a: pytest.fail("unnecessary remote profile fetch"))
    monkeypatch.setattr("app.services.buff_auth.get_buff_auth_lock", nullcontext)
    monkeypatch.setattr("app.services.buff_checkout_guard.buff_activity_guard", nullcontext)
    assert steam_auth.try_steam_auto_relogin(force=True, account_id="two", background=True)[0]
    assert writes == [(("sessionid=s; steamLoginSecure=2%7C%7Cnew", "s", "2"), {"account_id": "two"})]


def test_refresh_is_scheduled_fifteen_minutes_before_expiry():
    from app.services.steam_auth import steam_cookie_refresh_reason
    assert steam_cookie_refresh_reason({"cookies": cookie(expires=1000900), "cookie_refreshed_at": 1000000}, now=1000000) == "expiring"
    assert steam_cookie_refresh_reason({"cookies": cookie(expires=1000901), "cookie_refreshed_at": 1000000}, now=1000000) == ""


def test_proactive_refresh_keeps_valid_account_available_until_auth_rejection(monkeypatch, queued_account):
    queue, auth, target, credentials, now, logs = queued_account
    queue._pending.clear()
    credentials["cookies"] = cookie(expires=9999999999)
    account = {**target, **credentials}
    queue.request_refresh(account, "seven_days")
    monkeypatch.setattr(auth, "try_steam_auto_relogin", lambda **kw: (False, "network_error", "timeout"))
    queue.process_pending()
    assert not queue.is_history_account_blocked(account)
    queue.request_refresh(account, "auth_rejected")
    assert queue.is_history_account_blocked(account)
