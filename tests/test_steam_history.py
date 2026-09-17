def test_steam_history_cache_respects_refresh_and_retention_windows(tmp_path, monkeypatch):
    from app import steam_history

    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    data = {"success": 1, "lowest_sell_order": "100", "sell_order_graph": []}

    assert steam_history.set_cached_item_orders_histogram(
        "123", data, fetched_at=1_000
    )

    fresh = steam_history.get_cached_item_orders_histogram("123", now=1_000 + 11 * 3600)
    assert fresh is not None
    assert fresh["data"] == data
    assert fresh["age"] == 11 * 3600

    stale = steam_history.get_cached_item_orders_histogram("123", now=1_000 + 2 * 24 * 3600)
    assert stale is not None
    assert stale["data"] == data

    expired = steam_history.get_cached_item_orders_histogram(
        "123", now=1_000 + 7 * 24 * 3600 + 1
    )
    assert expired is None


def test_steam_history_cache_keeps_query_dimensions_separate(tmp_path, monkeypatch):
    from app import steam_history

    monkeypatch.setattr(steam_history, "_HISTORY_DB_PATH", tmp_path / "history.db")
    cn_data = {"success": 1, "currency": "CNY"}
    us_data = {"success": 1, "currency": "USD"}

    assert steam_history.set_cached_item_orders_histogram(
        "123", cn_data, country="CN", currency=23, fetched_at=100
    )
    assert steam_history.set_cached_item_orders_histogram(
        "123", us_data, country="US", currency=1, fetched_at=200
    )

    assert steam_history.get_cached_item_orders_histogram(
        "123", country="CN", currency=23, now=101
    )["data"] == cn_data
    assert steam_history.get_cached_item_orders_histogram(
        "123", country="US", currency=1, now=201
    )["data"] == us_data
