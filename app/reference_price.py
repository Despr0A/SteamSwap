"""Persistent first-observation guard for Steam reference prices."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_DB_PATH = _CONFIG_DIR / "steam_reference_prices.db"
_DB_INIT_LOCK = threading.Lock()

DEFAULT_MAX_CHANGE_RATIO = 0.15
DEFAULT_REQUIRED_OBSERVATIONS = 2


def _cache_key(market_hash_name: str, app_id: int) -> str:
    return json.dumps(
        [int(app_id), str(market_hash_name).strip()],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _connection() -> sqlite3.Connection:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    with _DB_INIT_LOCK:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_reference_prices (
                cache_key TEXT PRIMARY KEY,
                market_hash_name TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                last_price REAL NOT NULL,
                stable_observations INTEGER NOT NULL,
                last_observed_at REAL NOT NULL
            )
            """
        )
        conn.commit()
    return conn


def observe_reference_price(
    market_hash_name: str,
    price: Any,
    *,
    app_id: int = 730,
    max_change_ratio: float = DEFAULT_MAX_CHANGE_RATIO,
    required_observations: int = DEFAULT_REQUIRED_OBSERVATIONS,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Record a price observation and report whether it is stable enough.

    The first valid observation is deliberately never stable. A later
    observation counts only when its relative change from the preceding
    observation is within ``max_change_ratio``.
    """
    name = str(market_hash_name or "").strip()
    try:
        current_price = float(price)
        app_id = int(app_id)
        max_change_ratio = max(0.0, float(max_change_ratio))
        required_observations = max(2, int(required_observations))
    except (TypeError, ValueError, OverflowError):
        return {"stable": False, "first_seen": False, "reason": "参考价无效"}
    if not name or current_price <= 0:
        return {"stable": False, "first_seen": False, "reason": "参考价无效"}

    key = _cache_key(name, app_id)
    observed_at = time.time() if now is None else float(now)
    conn = None
    try:
        conn = _connection()
        # Serialize read-modify-write so concurrent pipeline workers cannot
        # count the same observation twice.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_price, stable_observations FROM steam_reference_prices WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            observations = 1
            conn.execute(
                """
                INSERT INTO steam_reference_prices
                    (cache_key, market_hash_name, app_id, last_price, stable_observations, last_observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, name, app_id, current_price, observations, observed_at),
            )
            conn.commit()
            return {
                "stable": False,
                "first_seen": True,
                "stable_observations": observations,
                "change_ratio": None,
                "reason": "首次观测，暂缓交易",
            }

        previous_price = float(row[0])
        previous_observations = max(1, int(row[1]))
        change_ratio = abs(current_price - previous_price) / max(previous_price, 1e-9)
        if change_ratio <= max_change_ratio:
            observations = previous_observations + 1
            reason = "参考价连续观测稳定"
        else:
            observations = 1
            reason = "参考价变动过大，重新观望"
        conn.execute(
            """
            UPDATE steam_reference_prices
            SET last_price = ?, stable_observations = ?, last_observed_at = ?
            WHERE cache_key = ?
            """,
            (current_price, observations, observed_at, key),
        )
        conn.commit()
        return {
            "stable": observations >= required_observations,
            "first_seen": False,
            "stable_observations": observations,
            "change_ratio": change_ratio,
            "reason": reason,
        }
    except (OSError, sqlite3.Error, ValueError, TypeError):
        # A cache failure must fail closed: do not trade on an unverified price.
        return {"stable": False, "first_seen": False, "reason": "参考价稳定性记录失败"}
    finally:
        if conn is not None:
            conn.close()
