import pytest


@pytest.fixture
def login_state(monkeypatch):
    from app.services import steam_auth

    calls = []
    monkeypatch.setattr(
        steam_auth, "_try_steam_auto_relogin_impl",
        lambda **kwargs: calls.append(kwargs) or (False, "network_error", "TLS reset WinError 10054"),
    )
    return steam_auth, calls


def test_network_failure_can_retry_immediately(login_state):
    service, calls = login_state
    first = service.try_steam_auto_relogin(force=True)
    second = service.try_steam_auto_relogin(force=True)
    assert first == second == (False, "network_error", "TLS reset WinError 10054")
    assert len(calls) == 2


def test_transient_network_failure_does_not_block_recovery(login_state, monkeypatch):
    service, calls = login_state
    assert service.try_steam_auto_relogin(force=True)[1] == "network_error"
    monkeypatch.setattr(service, "_try_steam_auto_relogin_impl", lambda **kwargs: (True, "auto_ok", "ready"))
    assert service.try_steam_auto_relogin(force=True)[0]


def test_history_candidates_queue_one_background_login(login_state, monkeypatch, tmp_path):
    service, calls = login_state
    from app.services import steam_client
    from app import steam_history
    from tests.test_steam_cookie_refresh import cookie
    from tests.test_steam_history_account_pool import _prepare_client

    _prepare_client(monkeypatch, [{"account_id": "one", "steam_id": "1", "cookies": cookie(expires=1)}])
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(steam_client, "_fetch_history", lambda *args, **kwargs: pytest.fail("expired cookie sent"))
    client = steam_client.SteamClient()
    assert client.fetch_history("Item A") is None
    assert client.fetch_history("Item B") is None
    assert len(calls) == 0
    from app.services import steam_refresh
    assert list(steam_refresh._pending) == ["one"]
    error = client.get_last_history_error("Item B")
    assert error["code"] == "ACCOUNTS_REFRESH_PENDING"
