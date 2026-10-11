import pytest
from sqlalchemy import text


def test_paid_purchases_exposes_only_confirmed_snapshot(monkeypatch):
    from app.routes import transactions

    monkeypatch.setattr(transactions, "get_purchases", lambda: [
        {"name": "Unpaid", "price": 1, "at": 3},
        {"name": "Paid", "price": 10, "market_price": 20, "at": 2,
         "purchase_ratio": 0.575, "payment_confirmed": True,
         "assetid": "private", "listing": True, "sale_price": 40},
    ])
    assert transactions.api_purchases() == {"purchases": [
        {"name": "Paid", "price": 10, "market_price": 20, "at": 2, "purchase_ratio": 0.575}
    ]}


def test_database_rejects_unconfirmed_and_preserves_purchase_snapshot(monkeypatch, tmp_path):
    from app import database

    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "paid.db")
    monkeypatch.setattr(database, "_engine", None)
    database.init_db()
    try:
        with pytest.raises(ValueError, match="confirmed"):
            database.db_append_purchase({"name": "Unpaid", "price": 10})
        assert database.db_get_purchases() == []
        database.db_append_purchase({
            "name": "Paid", "price": 10, "market_price": 20, "at": 123,
            "purchase_ratio": 0.575, "payment_confirmed": True,
        })
        [purchase] = database.db_get_purchases()
        database.db_update_purchase_by_id(purchase["_db_id"], {
            "sale_price": 99, "listing": True, "price": 99, "market_price": 99,
            "purchase_ratio": 99, "at": 999,
        })
        [updated] = database.db_get_purchases()
        assert updated["purchase_ratio"] == 0.575
        assert updated["price"] == 10
        assert updated["market_price"] == 20
        assert updated["at"] == 123
        with database.get_engine().connect() as connection:
            assert "sale" not in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars().all()
    finally:
        database.get_engine().dispose()


def test_legacy_paid_backup_preserves_snapshot_and_filters_manual_entries(monkeypatch, tmp_path):
    from app import database

    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "legacy.db")
    monkeypatch.setattr(database, "_engine", None)
    database.init_db()
    try:
        database.db_replace_transactions([
            {"name": "Legacy paid", "buff_order_id": "bill-1", "price": 10,
             "market_price": 20, "at": 123},
            {"name": "Manual", "price": 5, "at": 1},
            {"name": "Unpaid", "buff_order_id": "bill-2", "payment_confirmed": False,
             "price": 5, "at": 1},
        ], [])
        [saved] = database.db_get_purchases()
        assert saved["name"] == "Legacy paid"
        assert saved["at"] == 123
        assert saved["purchase_ratio"] == 0.575
        assert saved["payment_confirmed"] is True
    finally:
        database.get_engine().dispose()


def test_removed_management_endpoints_are_absent():
    from app.routes import transactions, static
    from fastapi import HTTPException

    assert [route.path for route in transactions.router.routes] == ["/api/purchases"]
    with pytest.raises(HTTPException) as error:
        static.static_or_index("api/transactions")
    assert error.value.status_code == 404
