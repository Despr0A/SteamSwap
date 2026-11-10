import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import steam_history
from app.services import steam_client


@pytest.fixture(autouse=True)
def isolated_history(monkeypatch, tmp_path):
    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    steam_client._history_cache.clear()
    monkeypatch.setattr(steam_client, "load_app_config_validated", lambda: {})
    monkeypatch.setattr("utils.money._load_exchange_rates", lambda: {"USD": 7.2})
    yield
    steam_client._history_cache.clear()


def point(hour, price, volume="1"):
    return [f"Oct 10 2026 {hour:02d}: +0", price, volume]


def response(*points, currency="CNY"):
    return {"history": list(points), "currency": currency}


def test_duplicate_and_overlapping_responses_update_without_losing_old_points():
    first = response(point(0, 10), point(1, 20))
    stats = steam_history.merge_price_history("Item", first, fetched_at=1000)
    assert stats == {"received": 2, "valid": 2, "changed": 2, "total": 2}
    stats = steam_history.merge_price_history("Item", first, fetched_at=1001)
    assert stats["changed"] == 0
    latest = response(point(1, 25, "3"), point(2, 30))
    stats = steam_history.merge_price_history("Item", latest, fetched_at=1002)
    assert stats["changed"] == 2
    assert stats["total"] == 3
    saved = steam_history.get_price_history("Item", now=1003)
    assert saved["history"] == [point(0, 10), point(1, 25, "3"), point(2, 30)]
    assert saved["fetched_at"] == 1002
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(steam_price_history)")]
        assert "account_id" not in columns
        assert "steam_id" not in columns


def test_currency_is_metadata_and_shared_points_are_normalized_to_cny():
    steam_history.merge_price_history("Item", response(point(0, 7.2)), fetched_at=1000)
    stats = steam_history.merge_price_history("Item", response(point(0, 1), currency="USD"), fetched_at=1001)
    assert stats["total"] == 1
    saved = steam_history.get_price_history("Item", now=1002)
    assert saved["currency"] == "CNY"
    assert saved["history"] == [point(0, 7.2)]
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        raw, currency = conn.execute("SELECT point_json, source_currency FROM steam_price_history").fetchone()
        assert currency == "USD"
        assert '1, "1"' in raw


@pytest.mark.parametrize("currency", [None, "UNKNOWN", "JPY"])
def test_cny_refresh_reuses_normalized_points_and_preserves_unconverted_rows(currency):
    steam_history.merge_price_history(
        "Item",
        response(point(0, 10), currency=currency),
        fetched_at=1000,
    )
    assert steam_history.get_price_history("Item", now=1001) is None

    stats = steam_history.merge_price_history(
        "Item", response(point(1, 20)), fetched_at=1002
    )
    saved = steam_history.get_price_history("Item", now=1003)

    assert stats["changed"] == 1
    assert stats["total"] == 2
    assert saved["history"] == [point(1, 20)]
    assert saved["unconverted_points"] == 1
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        assert conn.execute(
            "SELECT source_currency, price_cny, updated_at FROM steam_price_history "
            "WHERE market_hash_name = ? ORDER BY traded_at LIMIT 1",
            ("Item",),
        ).fetchone() == (currency, None, 1000)


@pytest.mark.parametrize("already_fetched", [False, True])
def test_client_reuses_history_with_unconverted_old_points(monkeypatch, already_fetched):
    steam_history.merge_price_history("Item", response(point(0, 10), currency=None), fetched_at=1000)
    if already_fetched:
        steam_history.merge_price_history("Item", response(point(1, 20)), fetched_at=1001)
    now = [1002.0]
    calls = []
    logs = []
    monkeypatch.setattr(steam_client.time, "time", lambda: now[0])
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *args: (
        calls.append(args) or response(point(1, 20))
    ))
    monkeypatch.setattr("app.state.log", lambda message, *args, **kwargs: logs.append(message))
    client = steam_client.SteamClient()
    assert client.fetch_history("Item") == [point(1, 20)]
    assert client.get_last_history_error("Item") is None
    assert len(calls) == (0 if already_fetched else 1)
    assert any("未换算=1" in message for message in logs)

    # Simulate a restart six hours later: SQLite must prevent another request.
    steam_client._history_cache.clear()
    now[0] += 6 * 3600
    assert steam_client.SteamClient().fetch_history("Item") == [point(1, 20)]
    assert len(calls) == (0 if already_fetched else 1)


def test_latest_unknown_currency_is_rejected_even_with_older_cny_points():
    steam_history.merge_price_history("Item", response(point(0, 10)), fetched_at=1000)
    steam_history.merge_price_history("Item", response(point(1, 20), currency="UNKNOWN"), fetched_at=1001)
    assert steam_history.get_price_history("Item", now=1002) is None


def test_history_requires_at_least_one_normalized_point():
    steam_history.merge_price_history("Item", response(point(0, 10)), fetched_at=1000)
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        conn.execute("UPDATE steam_price_history SET price_cny = NULL")
    assert steam_history.get_price_history("Item", now=1001) is None


def test_equivalent_time_zones_deduplicate_and_games_remain_separate():
    steam_history.merge_price_history("Item", response(point(0, 10)), fetched_at=1000)
    steam_history.merge_price_history("Item", response(["Oct 10 2026 08: +8", 20, "2"]), fetched_at=1001)
    steam_history.merge_price_history("Item", response(point(0, 30)), app_id=440, fetched_at=1002)
    assert len(steam_history.get_price_history("Item", now=1003)["history"]) == 1
    assert steam_history.get_price_history("Item", now=1003)["history"][0][1] == 20
    assert steam_history.get_price_history("Item", app_id=440, now=1003)["history"][0][1] == 30


def test_expiration_and_histogram_pruning_do_not_delete_price_history():
    steam_history.merge_price_history("Item", response(point(0, 10)), fetched_at=1000)
    refresh = steam_history.HISTORY_REFRESH_SECONDS
    assert steam_history.get_price_history("Item", now=1000 + refresh - 1) is not None
    assert steam_history.get_price_history("Item", now=1000 + refresh) is None
    steam_history.prune_cached_item_orders_histogram(now=1000 + 30 * 86400)
    assert steam_history.get_price_history("Item", now=1001)["history"] == [point(0, 10)]


def test_client_restart_and_account_change_reuse_same_persistent_history(monkeypatch):
    calls = []
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *args: (
        calls.append(args) or response(point(0, 10))
    ))
    assert steam_client.SteamClient().fetch_history("Item", return_currency=True)["history"] == [point(0, 10)]
    steam_client._history_cache.clear()
    monkeypatch.setattr(steam_client, "get_steam_credentials", lambda: {"steam_id": "different-account"})
    saved = steam_client.SteamClient().fetch_history("Item")
    assert saved == [point(0, 10)]
    assert len(calls) == 1


def test_client_default_history_cache_refreshes_after_twelve_hours(monkeypatch):
    now = [1000.0]
    calls = []
    monkeypatch.setattr(steam_client.time, "time", lambda: now[0])
    monkeypatch.setattr(
        steam_client.SteamClient,
        "_fetch_history_impl",
        lambda *args: calls.append(args) or response(point(0, 10)),
    )

    client = steam_client.SteamClient()
    assert client.fetch_history("Item") == [point(0, 10)]

    now[0] += 6 * 60 * 60
    assert client.fetch_history("Item") == [point(0, 10)]
    assert len(calls) == 1

    now[0] += steam_history.HISTORY_REFRESH_SECONDS
    assert client.fetch_history("Item") == [point(0, 10)]
    assert len(calls) == 2


def test_client_refresh_merges_points_and_failed_refresh_keeps_database(monkeypatch):
    results = iter([response(point(0, 10)), response(point(1, 20)), None])
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *args: next(results))
    client = steam_client.SteamClient(cache_ttl=0)
    assert client.fetch_history("Item") == [point(0, 10)]
    assert client.fetch_history("Item") == [point(0, 10), point(1, 20)]
    assert client.fetch_history("Item") is None
    assert steam_history.get_price_history("Item")["history"] == [point(0, 10), point(1, 20)]


def test_storage_failure_is_reported_and_response_is_not_cached(monkeypatch):
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *args: response(point(0, 10)))
    monkeypatch.setattr(steam_client, "merge_price_history", lambda *args, **kwargs: (
        (_ for _ in ()).throw(sqlite3.OperationalError("disk full"))
    ))
    logs = []
    monkeypatch.setattr("app.state.log", lambda message, *args, **kwargs: logs.append(message))
    assert steam_client.SteamClient().fetch_history("Item") is None
    assert steam_client._history_cache == {}
    assert any("disk full" in message for message in logs)


def test_unknown_currency_and_invalid_points_are_saved_raw_without_poisoning_shared_cache():
    data = response(point(0, 10), ["bad date", 20, "1"], currency="UNKNOWN")
    stats = steam_history.merge_price_history("Item", data, fetched_at=1000)
    assert stats["received"] == 2
    assert stats["valid"] == 1
    assert steam_history.get_price_history("Item", now=1001) is None
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        raw = conn.execute("SELECT response_json FROM steam_price_history_fetch").fetchone()[0]
        assert "bad date" in raw
        assert "UNKNOWN" in raw


def test_client_does_not_trade_on_unknown_currency_but_saves_response(monkeypatch):
    monkeypatch.setattr(steam_client.SteamClient, "_fetch_history_impl", lambda *args: response(point(0, 10), currency="UNKNOWN"))
    assert steam_client.SteamClient().fetch_history("Item", return_currency=True) is None
    assert steam_client._history_cache == {}
    with sqlite3.connect(steam_history._HISTORY_DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM steam_price_history").fetchone()[0] == 1


def test_concurrent_merges_preserve_all_distinct_points():
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(steam_history.merge_price_history, "Item", response(point(hour, hour + 1))) for hour in range(8)]
        for future in futures:
            future.result()
    saved = steam_history.get_price_history("Item")
    assert saved["history"] == [point(hour, hour + 1) for hour in range(8)]
