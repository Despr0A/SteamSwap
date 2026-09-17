import hashlib
import threading
import time
from typing import Any, Dict, List, Optional, Union
from app.services.retry import with_retry
from app.config_loader import (
    get_steam_credentials,
    get_steam_history_credentials,
    load_app_config_validated,
)
from steam.client import (
    fetch_history as _fetch_history,
    market_hash_name_from_listing_url as _market_hash_name_from_listing_url,
)
steam_timeout = 15
steam_retry_attempts = 2
steam_account_cooldown_seconds = 5 * 60
steam_account_invalid_cooldown_seconds = 60 * 60
_history_cache: Dict[str, tuple] = {}
_history_cache_ttl = 300
_history_cache_max = 200
_history_pool_lock = threading.Lock()
_history_pool_cursor = 0
_history_pool_cooldowns: Dict[str, float] = {}


def _account_key(account: dict) -> str:
    identity = str(account.get("steam_id") or "").strip()
    if identity:
        return identity
    return hashlib.sha256(str(account.get("cookies") or "").encode("utf-8")).hexdigest()


def _history_accounts() -> list[dict]:
    pool = get_steam_history_credentials()
    primary = get_steam_credentials() or {}
    try:
        from app.accounts import get_current_account, list_accounts

        current = get_current_account() or {}
        current_currency = str(current.get("currency_code") or "").strip().upper()
        account_metadata = {
            str(item.get("id") or "").strip(): item
            for item in list_accounts()
            if isinstance(item, dict)
        }
        account_by_steam_id = {
            str(item.get("steam_id") or "").strip(): item
            for item in list_accounts()
            if isinstance(item, dict) and str(item.get("steam_id") or "").strip()
        }
        def metadata_for(account: dict) -> dict:
            return account_metadata.get(str(account.get("account_id") or "").strip(), {}) or account_by_steam_id.get(str(account.get("steam_id") or "").strip(), {})
        pool = [
            account for account in pool
            if metadata_for(account).get("id") == current.get("id")
            or (
                current_currency
                and metadata_for(account).get("region_check_ok") is True
                and str(metadata_for(account).get("currency_code") or "").strip().upper() == current_currency
            )
        ]
    except Exception:
        pool = []
    if primary.get("cookies"):
        pool = [*pool, {
            "account_id": "primary",
            "steam_id": primary.get("steam_id", ""),
            "cookies": primary.get("cookies", ""),
        }]
    result = []
    seen = set()
    for account in pool:
        if not isinstance(account, dict) or not str(account.get("cookies") or "").strip():
            continue
        key = _account_key(account)
        if key in seen:
            continue
        seen.add(key)
        account = dict(account)
        account["_key"] = key
        result.append(account)
    return result


def _select_history_accounts() -> list[dict]:
    global _history_pool_cursor
    accounts = _history_accounts()
    if not accounts:
        accounts = [{"account_id": "anonymous", "cookies": "", "_key": "anonymous"}]
    now = time.monotonic()
    with _history_pool_lock:
        available = [a for a in accounts if _history_pool_cooldowns.get(a["_key"], 0) <= now]
        if not available:
            return []
        start = _history_pool_cursor % len(available)
        ordered = available[start:] + available[:start]
        _history_pool_cursor = (_history_pool_cursor + 1) % len(available)
        return ordered


def _cooldown_history_account(account: dict, seconds: float) -> None:
    with _history_pool_lock:
        _history_pool_cooldowns[account["_key"]] = time.monotonic() + seconds
class SteamClient:
    def __init__(
        self,
        timeout_sec: int = steam_timeout,
        cache_ttl: int = _history_cache_ttl,
        cache_max: int = _history_cache_max,
    ) -> None:
        self._timeout = timeout_sec
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max
    def fetch_history(
        self,
        market_hash_name: str,
        app_id: int = 730,
        return_currency: bool = False,
    ) -> Union[Optional[List], Optional[Dict]]:
        key = f"{market_hash_name}:{app_id}:{return_currency}"
        now = time.time()
        if key in _history_cache:
            data, ts = _history_cache[key]
            if now - ts < self._cache_ttl:
                return data
        result = self._fetch_history_impl(market_hash_name, app_id, return_currency)
        if result is not None:
            if len(_history_cache) >= self._cache_max:
                oldest = min(_history_cache.items(), key=lambda x: x[1][1])
                del _history_cache[oldest[0]]
            _history_cache[key] = (result, now)
        return result
    def _fetch_history_impl(
        self,
        market_hash_name: str,
        app_id: int,
        return_currency: bool,
    ) -> Union[Optional[List], Optional[Dict]]:
        cfg = load_app_config_validated()
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        accounts = _select_history_accounts()
        for account in accounts:
            cookies = str(account.get("cookies") or "").strip() or None
            for attempt in range(steam_retry_attempts):
                failed = attempt > 0
                proxies = pm.get_proxies_for_request(failed=failed)
                if proxies is None and not pm.is_proxy_enabled():
                    proxy_url = cfg.get("steam", {}).get("proxy")
                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                try:
                    result, status_code = _fetch_history(
                        market_hash_name,
                        app_id=app_id,
                        timeout=self._timeout,
                        return_currency=return_currency,
                        return_status=True,
                        cookies=cookies,
                        proxies=proxies,
                    )
                except TypeError:
                    result = _fetch_history(
                        market_hash_name,
                        app_id=app_id,
                        timeout=self._timeout,
                        return_currency=return_currency,
                        cookies=cookies,
                        proxies=proxies,
                    )
                    status_code = 200 if result is not None else None
                if result is not None:
                    return result
                if status_code == 429:
                    _cooldown_history_account(account, steam_account_cooldown_seconds)
                    from app.state import log
                    log(
                        f"[SteamClient] 历史账号触发 429，冷却 {steam_account_cooldown_seconds} 秒 "
                        f"account={account.get('account_id') or account['_key'][:8]}",
                        "warn",
                        category="steam",
                    )
                    break
                if status_code in (401, 403):
                    _cooldown_history_account(account, steam_account_invalid_cooldown_seconds)
                    from app.state import log
                    log(
                        f"[SteamClient] 历史账号鉴权失败，冷却 {steam_account_invalid_cooldown_seconds} 秒 "
                        f"account={account.get('account_id') or account['_key'][:8]} status={status_code}",
                        "warn",
                        category="steam",
                    )
                    break
                from app.state import log
                log(f"[SteamClient] 历史数据请求失败 account={account.get('account_id') or account['_key'][:8]} (attempt={attempt+1}/{steam_retry_attempts}) proxies={proxies} status={status_code}", "debug", category="proxy")
                if attempt < steam_retry_attempts - 1:
                    from utils.delay import jittered_sleep
                    jittered_sleep(1.0)
        return None
    @staticmethod
    def market_hash_name_from_listing_url(url: str) -> Optional[str]:
        return _market_hash_name_from_listing_url(url)
def create_steam_client(config: Optional[dict] = None) -> SteamClient:
    timeout = 15
    if config:
        timeout = int(config.get("steam", {}).get("timeout", steam_timeout))
    return SteamClient(timeout_sec=timeout)
