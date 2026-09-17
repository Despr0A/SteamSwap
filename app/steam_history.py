"""Persistent cache for Steam market order history responses.

The histogram endpoint is relatively expensive and can trigger Steam rate
limits when several inventory items are priced in one batch.  Keep the raw
successful response in a small, dedicated SQLite database so the main
transaction database remains independent from market-cache retention.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

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
