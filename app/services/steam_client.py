import hashlib
import math
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Union
from steam.cookies import cookie_metadata
from app.steam_history import HISTORY_REFRESH_SECONDS, get_price_history, merge_price_history
from utils.diagnostics import endpoint, exception_details, http_code, one_line, redact
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
steam_all_accounts_wait_seconds = 30
_history_cache: Dict[str, tuple] = {}
# Historical price points are persisted and used for multi-day stability
# analysis, so refreshing them every few minutes is unnecessary.
_history_cache_ttl = HISTORY_REFRESH_SECONDS
_history_cache_max = 200
_history_pool_lock = threading.Lock()
_history_pool_cursor = 0
_history_pool_cooldowns: Dict[str, float] = {}
_history_pool_cooldown_status: Dict[str, int] = {}


def _account_key(account: dict) -> str:
    identity = str(account.get("steam_id") or "").strip()
    if identity:
        return identity
    return hashlib.sha256(str(account.get("cookies") or "").encode("utf-8")).hexdigest()


def _credential_diagnostics(account: dict) -> dict:
    metadata = cookie_metadata(str(account.get("cookies") or ""))
    identity = metadata.get("steam_id")
    expected = str(account.get("steam_id") or "").strip()
    return {"cookie_present": "steamLoginSecure=" in str(account.get("cookies") or ""),
            "cookie_identity_matches": identity == expected if identity and expected else None,
            "cookie_expired": metadata["expires_at"] <= time.time() if "expires_at" in metadata else None}


def _history_accounts() -> list[dict]:
    pool = get_steam_history_credentials()
    primary = get_steam_credentials() or {}
    # History credentials are selected by valid Cookie/identity only.  Wallet
    # setup is optional for new accounts, and currency is converted to CNY
    # after the response using the daily exchange-rate snapshot.
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
    accounts = [a for a in accounts if not _history_auth_unavailable(a)]
    now = time.monotonic()
    with _history_pool_lock:
        available = [a for a in accounts if _history_pool_cooldowns.get(a["_key"], 0) <= now]
        if not available:
            return []
        start = _history_pool_cursor % len(available)
        ordered = available[start:] + available[:start]
        _history_pool_cursor = (_history_pool_cursor + 1) % len(available)
        return ordered


def _cooldown_history_account(account: dict, seconds: float, status: int = 429) -> None:
    with _history_pool_lock:
        _history_pool_cooldowns[account["_key"]] = time.monotonic() + seconds
        _history_pool_cooldown_status[account["_key"]] = status


def _wait_for_history_accounts() -> bool:
    from app.state import is_stop_requested, log
    accounts = _history_accounts() or [{"account_id": "anonymous", "_key": "anonymous"}]
    now = time.monotonic()
    with _history_pool_lock:
        cooldowns = [
            (account.get("account_id") or account["_key"][:8],
             math.ceil(max(0, _history_pool_cooldowns.get(account["_key"], 0) - now)),
             _history_pool_cooldown_status.get(account["_key"], 429))
            for account in accounts
        ]
    reason = "全部账号均已进入429冷却" if all(status == 429 for _, _, status in cooldowns) else "全部账号均在冷却（含鉴权失败）"
    remaining = ", ".join(f"account={account} 剩余 {seconds} 秒" for account, seconds, _ in cooldowns)
    log(f"[SteamClient] {reason}；{remaining}；任务休眠 {steam_all_accounts_wait_seconds} 秒", "warn", category="steam")
    deadline = time.monotonic() + steam_all_accounts_wait_seconds
    while time.monotonic() < deadline:
        if is_stop_requested():
            return False
        time.sleep(min(1.0, deadline - time.monotonic()))
    return not is_stop_requested()


def _history_auth_unavailable(account: dict) -> bool:
    from app.services import steam_refresh

    expired = _credential_diagnostics(account).get("cookie_expired")
    if expired:
        steam_refresh.request_refresh(account, "expired")
    return bool(expired or steam_refresh.is_history_account_blocked(account))


def _resolve_history_currency(result, account: dict):
    if not isinstance(result, dict) or result.get("currency"):
        return result
    from app.accounts import get_account, get_current_account
    from app.state import log

    account_id = account.get("account_id")
    metadata = (get_current_account() if account_id == "primary" else get_account(account_id)) or {}
    cookies = str(account.get("cookies") or "")
    identity = cookie_metadata(cookies).get("steam_id")
    expected_identity = str(metadata.get("steam_id") or account.get("steam_id") or "").strip()
    if not identity or not expected_identity or identity != expected_identity:
        return result
    currency = str(metadata.get("currency_code") or "").strip().upper()
    currency_symbol = str(result.get("currency_symbol") or "").strip()
    if currency_symbol in ("\u00a5", "\uffe5") and currency not in ("CNY", "JPY"):
        # Steam uses the same yen sign for CNY and JPY.  Wallet metadata is
        # deliberately not queried here: history accounts can be brand new
        # and have no wallet.  The service's default market is CNY, while an
        # explicitly stored account currency remains respected when present.
        currency = "CNY"
    elif not currency_symbol and not currency:
        currency = "CNY"
    elif currency_symbol and currency_symbol not in ("\u00a5", "\uffe5"):
        return result
    account_label = metadata.get("id") or account_id or expected_identity
    log(
        f"[SteamClient] account={account_label} 历史货币字段={currency_symbol or '(空)'}，"
        f"按无需钱包的历史规则解析为 {currency}",
        "debug",
        category="steam",
    )
    return {**result, "currency": currency}


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
        self._request_context = threading.local()

    def get_last_history_error(self, market_hash_name: str, app_id: int = 730) -> Optional[dict]:
        error = getattr(self._request_context, "error", None)
        if error and error.get("item") == market_hash_name and error.get("app_id") == app_id:
            return redact(error)
        return None

    def get_last_history_report(self, market_hash_name: str, app_id: int = 730) -> dict:
        if (getattr(self._request_context, "item", None) != market_hash_name
                or getattr(self._request_context, "app_id", None) != app_id):
            return {}
        return redact({
            **getattr(self._request_context, "report", {}),
            "request_id": getattr(self._request_context, "request_id", ""),
            "diagnostic": self.get_last_history_error(market_hash_name, app_id),
        })

    def _record_history_error(self, code: str, stage: str, *, emit_log: bool = True, **details) -> dict:
        from app.state import log
        error = {
            "request_id": getattr(self._request_context, "request_id", ""),
            "operation": "steam.pricehistory", "source": "app/services/steam_client.py",
            "code": code, "stage": stage,
            "item": getattr(self._request_context, "item", ""),
            "app_id": getattr(self._request_context, "app_id", 730),
            **details,
        }
        error = redact(error)
        counts = getattr(self._request_context, "error_counts", {})
        counts[code] = counts.get(code, 0) + 1
        self._request_context.error_counts = counts
        error["failure_counts"] = dict(counts)
        self._request_context.error = error
        if emit_log:
            log(f"[SteamClient] {one_line(error)}", "warn", category="steam", diagnostic=error)
        return error
    def fetch_history(
        self,
        market_hash_name: str,
        app_id: int = 730,
        return_currency: bool = False,
    ) -> Union[Optional[List], Optional[Dict]]:
        self._request_context.error = None
        self._request_context.error_counts = {}
        self._request_context.request_id = uuid.uuid4().hex[:12]
        self._request_context.item = market_hash_name
        self._request_context.app_id = app_id
        self._request_context.report = {
            "source": "local", "network_attempted": False, "attempts": 0,
            "ttl_seconds": self._cache_ttl,
        }
        key = f"{market_hash_name}:{app_id}"
        now = time.time()
        if key in _history_cache:
            data, ts = _history_cache[key]
            if 0 <= now - ts < self._cache_ttl:
                self._request_context.report.update(source="memory_cache", fetched_at=ts, age_seconds=now-ts)
                return data if return_currency else data["history"]
        from app.state import log
        try:
            stored = get_price_history(market_hash_name, app_id=app_id, max_age=self._cache_ttl, now=now)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._record_history_error("HISTORY_CACHE_READ_ERROR", "cache_read", **exception_details(exc), action="尝试远端请求")
            stored = None
        if stored is not None:
            result = {"history": stored["history"], "currency": stored["currency"]}
            fetched_at = stored["fetched_at"]
            self._request_context.report.update(source="database_cache", fetched_at=fetched_at, age_seconds=now-fetched_at)
            log(f"[SteamClient] 复用本地历史行情: {market_hash_name} {len(result['history'])} 条", "debug", category="steam")
        else:
            self._request_context.report["source"] = "refresh"
            result = self._fetch_history_impl(market_hash_name, app_id, True)
            if result is None:
                if self.get_last_history_error(market_hash_name, app_id) is None:
                    self._record_history_error("HISTORY_UNAVAILABLE", "fetch", detail="请求适配器未提供失败详情", action="核查 steam 类别日志与适配器")
                return None
            if isinstance(result, list):
                # Legacy adapters return only points.  Keep the historical
                # CNY default so an account without wallet metadata can still
                # participate in price-history analysis.
                result = {"history": result, "currency": "CNY"}
            fetched_at = time.time()
            self._request_context.report.update(fetched_at=fetched_at, age_seconds=0)
            try:
                stats = merge_price_history(
                    market_hash_name, result, app_id=app_id, fetched_at=fetched_at,
                    # History normalization must use the shared daily rate
                    # snapshot; do not fall back to a per-account wallet or
                    # static USD rate here.
                    usd_to_cny_rate=None,
                )
                log(
                    f"[SteamClient] 历史行情已入库: {market_hash_name} 返回={stats['received']} "
                    f"新增或更新={stats['changed']} 总计={stats['total']}",
                    "info", category="steam",
                )
                stored = get_price_history(market_hash_name, app_id=app_id, now=fetched_at)
            except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
                self._record_history_error("HISTORY_STORAGE_ERROR", "persist", **exception_details(exc), action="检查历史数据库路径、磁盘与权限")
                return None
            if stored is not None:
                result = {"history": stored["history"], "currency": stored["currency"]}
            else:
                code = "INVALID_HISTORY_POINTS" if stats["valid"] == 0 else "HISTORY_CURRENCY_UNSUPPORTED"
                self._record_history_error(code, "normalize", detail=f"currency={result.get('currency')} received={stats['received']} valid={stats['valid']} total={stats['total']}; 原始响应已保存但不能用于人民币行情", action="检查币种、汇率与历史时间/价格字段")
                return None
        if result is not None:
            if stored.get("unconverted_points", 0):
                log(
                    f"[SteamClient] 历史行情跳过未换算记录: {market_hash_name} "
                    f"未换算={stored['unconverted_points']} 可用={len(result['history'])}；"
                    "原始记录已保留，仅使用已归一化的人民币行情",
                    "info", category="steam",
                )
            self._request_context.error = None
            if len(_history_cache) >= self._cache_max:
                oldest = min(_history_cache.items(), key=lambda x: x[1][1])
                del _history_cache[oldest[0]]
            _history_cache[key] = (result, fetched_at)
        return result if return_currency else result["history"]
    def _fetch_history_impl(
        self,
        market_hash_name: str,
        app_id: int,
        return_currency: bool,
    ) -> Union[Optional[List], Optional[Dict]]:
        cfg = load_app_config_validated()
        from app.state import is_stop_requested, log
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        request_context = f"request_id={getattr(self._request_context, 'request_id', '')} item={market_hash_name} app_id={app_id}"
        while True:
            if is_stop_requested():
                self._record_history_error("CANCELLED", "stop", action="任务已停止")
                return None
            accounts = _select_history_accounts()
            if not accounts:
                pool = _history_accounts()
                if pool and all(_history_auth_unavailable(a) for a in pool):
                    self._record_history_error(
                        "ACCOUNTS_REFRESH_PENDING", "account_select", emit_log=False,
                        action="账号暂不可用，后台刷新或等待人工登录；暂缓本次候选检查",
                    )
                    return None
                if not _wait_for_history_accounts():
                    if not getattr(self._request_context, "error", None):
                        self._record_history_error("ACCOUNTS_COOLING_DOWN", "account_select", action="等待账号冷却结束或核查鉴权")
                    return None
                continue
            retry_cooling = False
            for account in accounts:
                if is_stop_requested():
                    return None
                cookies = str(account.get("cookies") or "").strip() or None
                if _history_auth_unavailable(account):
                    continue
                used_proxy_urls = set()
                proxies = pm.get_proxies_for_request()
                if proxies is None and not pm.is_proxy_enabled():
                    proxy_url = cfg.get("steam", {}).get("proxy")
                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                if pm.should_always_use_proxy() and proxies is None:
                    self._record_history_error("NO_AVAILABLE_PROXY", "proxy_select", account=account.get("account_id"), action="强制代理模式没有可用节点，检查代理检测日志")
                    return None
                rate_limited = False
                attempt = 0
                while True:
                    if is_stop_requested():
                        return None
                    attempt += 1
                    if proxies:
                        used_proxy_urls.update(proxies.values())
                    report = getattr(self._request_context, "report", {})
                    report.update(
                        network_attempted=True, attempts=report.get("attempts", 0) + 1,
                        endpoint="steamcommunity.com/market/pricehistory/", method="GET",
                        account=account.get("account_id"),
                        proxy=endpoint(proxies.get("https") or proxies.get("http") or "") if proxies else "direct/system",
                    )
                    try:
                        response = _fetch_history(
                            market_hash_name, app_id=app_id, timeout=self._timeout,
                            return_currency=return_currency, return_status=True,
                            return_metadata=True, cookies=cookies, proxies=proxies,
                            return_diagnostics=True,
                        )
                        if isinstance(response, tuple) and len(response) == 3:
                            result, status_code, metadata = response
                            if isinstance(metadata, dict):
                                request_error = metadata
                                auth_refresh_redirected = bool(metadata.get("auth_refresh_redirected"))
                            else:
                                auth_refresh_redirected = bool(metadata)
                                request_error = {}
                        else:
                            result, status_code = response
                            auth_refresh_redirected = False
                            request_error = {}
                    except TypeError as exc:
                        if "unexpected keyword argument" not in str(exc):
                            self._record_history_error("HISTORY_ADAPTER_ERROR", "adapter", **exception_details(exc), account=account.get("account_id"), attempt=attempt)
                            return None
                        result = _fetch_history(
                            market_hash_name,
                            app_id=app_id,
                            timeout=self._timeout,
                            return_currency=return_currency,
                            cookies=cookies,
                            proxies=proxies,
                        )
                        status_code = 200 if result is not None else None
                        auth_refresh_redirected = False
                        request_error = {}
                    report["http_status"] = status_code
                    if result is not None:
                        return _resolve_history_currency(result, {**account, "cookies": cookies})
                    code = request_error.get("code") or (http_code(status_code, auth_refresh_redirected) if status_code and status_code != 200 else "HISTORY_UNAVAILABLE")
                    stage = request_error.get("stage") or ("http" if status_code else "request")
                    context = {key: value for key, value in request_error.items() if key not in {"code", "stage", "request_id", "item", "app_id", "operation"}}
                    context.update(_credential_diagnostics({**account, "cookies": cookies}))
                    if request_error.get("request_id"):
                        context["attempt_id"] = request_error["request_id"]
                    context.update(
                        status=status_code, account=account.get("account_id") or account["_key"][:8],
                        attempt=attempt,
                        proxy=request_error.get("proxy") or (endpoint(proxies.get("https") or proxies.get("http") or "") if proxies else "direct/system"),
                        auth_refresh_redirected=auth_refresh_redirected,
                        action="按策略重试或切换账号/代理",
                    )
                    self._record_history_error(code, stage, **context)
                    if status_code in (401, 403) or (status_code == 429 and auth_refresh_redirected):
                        from app.services.steam_refresh import request_refresh
                        request_refresh(account, "auth_rejected")
                        break
                    if status_code == 429:
                        rate_limited = True
                        log(
                            f"[SteamClient] {request_context} 历史账号触发 429，将切换未尝试的代理 "
                            f"account={account.get('account_id') or account['_key'][:8]} attempt={attempt} "
                            f"auth_refresh_redirected={auth_refresh_redirected}",
                            "warn",
                            category="steam",
                        )
                    if rate_limited:
                        proxies = pm.get_next_proxy_dict(excluded=used_proxy_urls) if pm.is_proxy_enabled() else None
                        if proxies is None:
                            _cooldown_history_account(account, steam_account_cooldown_seconds)
                            retry_cooling = True
                            log(
                                f"[SteamClient] {request_context} 429 后可用代理已耗尽，账号冷却 {steam_account_cooldown_seconds} 秒 "
                                f"account={account.get('account_id') or account['_key'][:8]}",
                                "warn", category="steam",
                            )
                            break
                        from urllib.parse import urlsplit
                        proxy_endpoint = urlsplit(proxies.get("https") or proxies.get("http") or "")
                        log(f"[SteamClient] {request_context} 429 后切换代理重试: {proxy_endpoint.hostname}:{proxy_endpoint.port}", "info", category="steam")
                    else:
                        if attempt >= steam_retry_attempts:
                            break
                        proxies = pm.get_proxies_for_request(failed=True)
                        if proxies is None and not pm.is_proxy_enabled():
                            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    from utils.delay import jittered_sleep
                    jittered_sleep(1.0)
            if retry_cooling and not _select_history_accounts():
                continue
            error = getattr(self._request_context, "error", None)
            if error:
                error["action"] = "本次可用账号/重试已结束，跳过候选；按 request_id 查找各次请求"
                log(f"[SteamClient] 历史读取结束: {one_line(error)}", "warn", category="steam", diagnostic=error)
            return None
    @staticmethod
    def market_hash_name_from_listing_url(url: str) -> Optional[str]:
        return _market_hash_name_from_listing_url(url)
def create_steam_client(config: Optional[dict] = None) -> SteamClient:
    timeout = 15
    if config:
        timeout = int(config.get("steam", {}).get("timeout", steam_timeout))
    return SteamClient(timeout_sec=timeout)
