"""Persistent Steam price history and market order response cache.

The histogram endpoint is relatively expensive and can trigger Steam rate
limits when several inventory items are priced in one batch.  Keep the raw
successful response in a small, dedicated SQLite database so the main
transaction database remains independent from market-cache retention.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional
from utils.money import apply_currency
from utils.time import parse_steam_history_date

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_HISTORY_DB_PATH = _CONFIG_DIR / "steam_market_history.db"
HISTORY_RETENTION_SECONDS = 7 * 24 * 60 * 60
HISTORY_REFRESH_SECONDS = 12 * 60 * 60
_DB_INIT_LOCK = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_HISTORY_DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    with _DB_INIT_LOCK:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_market_history (
                cache_key TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                fetched_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_price_history (
                app_id INTEGER NOT NULL,
                market_hash_name TEXT NOT NULL,
                traded_at INTEGER NOT NULL,
                point_json TEXT NOT NULL,
                source_currency TEXT,
                price_cny REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (app_id, market_hash_name, traded_at)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_price_history_fetch (
                app_id INTEGER NOT NULL,
                market_hash_name TEXT NOT NULL,
                response_json TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (app_id, market_hash_name)
            )
            """
        )
        conn.commit()
    return conn


def _cache_key(
    item_nameid: str,
    country: str,
    language: str,
    currency: int,
) -> str:
    return json.dumps(
        [str(item_nameid), str(country), str(language), int(currency)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def get_cached_item_orders_histogram(
    item_nameid: str,
    *,
    country: str = "CN",
    language: str = "english",
    currency: int = 23,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Return a cached response younger than seven days, if available.

    The caller uses ``fetched_at`` to decide whether a refresh is needed.
    Expired or malformed rows are removed eagerly.
    """
    if not str(item_nameid or "").strip():
        return None
    key = _cache_key(item_nameid, country, language, currency)
    current_time = time.time() if now is None else float(now)
    conn = None
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT data_json, fetched_at FROM steam_market_history WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        data_json, fetched_at = row
        age = current_time - float(fetched_at)
        if age > HISTORY_RETENTION_SECONDS:
            conn.execute("DELETE FROM steam_market_history WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            conn.execute("DELETE FROM steam_market_history WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        if not isinstance(data, dict):
            conn.execute("DELETE FROM steam_market_history WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        return {"data": data, "fetched_at": float(fetched_at), "age": max(0.0, age)}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        if conn is not None:
            conn.close()


def merge_price_history(
    market_hash_name: str,
    data: dict[str, Any],
    *,
    app_id: int = 730,
    fetched_at: Optional[float] = None,
    usd_to_cny_rate: Optional[float] = None,
) -> dict[str, int]:
    """Merge shared market points; keep raw values alongside their CNY prices."""
    name = str(market_hash_name).strip()
    history = data.get("history")
    if not name or not isinstance(history, list):
        raise ValueError("Invalid Steam price history response")
    observed_at = time.time() if fetched_at is None else float(fetched_at)
    currency = str(data.get("currency") or "").strip().upper() or None
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
    points = {}
    for point in history:
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            continue
        date = parse_steam_history_date(str(point[0]))
        try:
            price = float(point[1])
            volume = float(point[2])
        except (TypeError, ValueError):
            continue
        if date is None or not math.isfinite(price) or not math.isfinite(volume) or price < 0 or volume < 0:
            continue
        converted, code = apply_currency([price], currency, usd_to_cny_rate)
        price_cny = converted[0] if currency and code == "CNY" else None
        if price_cny is not None and not math.isfinite(price_cny):
            price_cny = None
        points[int(date.timestamp())] = (
            json.dumps(point, ensure_ascii=False, allow_nan=False), currency, price_cny,
        )
    conn = _get_connection()
    try:
        # Serialize the merge and timestamp update so readers never see half a response.
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO steam_price_history
                    (app_id, market_hash_name, traded_at, point_json, source_currency, price_cny, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_id, market_hash_name, traded_at) DO UPDATE SET
                    point_json = excluded.point_json,
                    source_currency = excluded.source_currency,
                    price_cny = excluded.price_cny,
                    updated_at = excluded.updated_at
                WHERE steam_price_history.point_json != excluded.point_json
                   OR steam_price_history.source_currency IS NOT excluded.source_currency
                   OR steam_price_history.price_cny IS NOT excluded.price_cny
                """,
                [(int(app_id), name, stamp, *values, observed_at) for stamp, values in points.items()],
            )
            changed = conn.total_changes - before
            conn.execute(
                """
                INSERT INTO steam_price_history_fetch(app_id, market_hash_name, response_json, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(app_id, market_hash_name) DO UPDATE SET
                    response_json = excluded.response_json,
                    fetched_at = excluded.fetched_at
                """,
                (int(app_id), name, serialized, observed_at),
            )
            total = conn.execute(
                "SELECT COUNT(*) FROM steam_price_history WHERE app_id = ? AND market_hash_name = ?",
                (int(app_id), name),
            ).fetchone()[0]
        return {"received": len(history), "valid": len(points), "changed": changed, "total": total}
    finally:
        conn.close()


def get_price_history(
    market_hash_name: str,
    *,
    app_id: int = 730,
    max_age: float = HISTORY_REFRESH_SECONDS,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Read fresh normalized points; retain unconverted raw rows for later use."""
    current_time = time.time() if now is None else float(now)
    conn = _get_connection()
    try:
        # A single read transaction pairs the points with their refresh timestamp.
        with conn:
            conn.execute("BEGIN")
            meta = conn.execute(
                "SELECT response_json, fetched_at FROM steam_price_history_fetch WHERE app_id = ? AND market_hash_name = ?",
                (int(app_id), str(market_hash_name).strip()),
            ).fetchone()
            if meta is None or not 0 <= current_time - meta[1] < max_age:
                return None
            response = json.loads(meta[0])
            currency = response.get("currency")
            _, converted_currency = apply_currency([1.0], currency, None)
            if not response.get("history") or not currency or converted_currency != "CNY":
                return None
            rows = conn.execute(
                "SELECT point_json, price_cny FROM steam_price_history WHERE app_id = ? AND market_hash_name = ? ORDER BY traded_at",
                (int(app_id), str(market_hash_name).strip()),
            ).fetchall()
            history = []
            for raw, price in rows:
                # An older response may lack currency for points no longer
                # returned by Steam. Keep those raw rows without blocking the
                # normalized series or guessing their original currency.
                if price is None:
                    continue
                point = json.loads(raw)
                point[1] = price
                history.append(point)
            if not history:
                return None
        return {
            "history": history, "currency": "CNY", "fetched_at": meta[1],
            "unconverted_points": len(rows) - len(history),
        }
    finally:
        conn.close()


def set_cached_item_orders_histogram(
    item_nameid: str,
    data: dict[str, Any],
    *,
    country: str = "CN",
    language: str = "english",
    currency: int = 23,
    fetched_at: Optional[float] = None,
) -> bool:
    """Store one successful histogram response and its fetch timestamp."""
    if not str(item_nameid or "").strip() or not isinstance(data, dict):
        return False
    try:
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return False
    key = _cache_key(item_nameid, country, language, currency)
    conn = None
    try:
        conn = _get_connection()
        conn.execute(
            """
            INSERT INTO steam_market_history(cache_key, data_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                data_json = excluded.data_json,
                fetched_at = excluded.fetched_at
            """,
            (key, serialized, time.time() if fetched_at is None else float(fetched_at)),
        )
        conn.commit()
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False
    finally:
        if conn is not None:
            conn.close()


def prune_cached_item_orders_histogram(*, now: Optional[float] = None) -> int:
    """Delete rows older than the seven-day retention window."""
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - HISTORY_RETENTION_SECONDS
    conn = None
    try:
        conn = _get_connection()
        cursor = conn.execute(
            "DELETE FROM steam_market_history WHERE fetched_at < ?", (cutoff,)
        )
        conn.commit()
        return max(0, int(cursor.rowcount))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return 0
    finally:
        if conn is not None:
            conn.close()
