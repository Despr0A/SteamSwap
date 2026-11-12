"""Per-account recovery queue, drained serially by the Steam refresh worker."""
import hashlib
import threading
import time

from app.accounts import get_account, get_current_account
from app.config_loader import load_app_config_validated
from app.services import steam_auth
from app.state import log

_lock = threading.Lock()
_pending = {}
wake = threading.Event()
_MANUAL_STATUSES = {"no_account", "no_creds", "wrong_creds", "need_2fa", "captcha", "identity_mismatch"}
_PROACTIVE_REASONS = {"seven_days", "expiring"}


def _account_id(account: dict) -> str:
    account_id = str(account.get("account_id") or account.get("id") or "")
    if account_id == "primary":
        current = get_current_account() or {}
        return str(current.get("id") or "") if current.get("steam_id") == account.get("steam_id") else ""
    return account_id


def _fingerprint(account: dict, cookies: str) -> str:
    guard = (load_app_config_validated().get("steam_guard") or {}).get("shared_secret")
    values = (account.get("username"), account.get("password"), account.get("steam_id"), guard, cookies)
    return hashlib.sha256(repr(values).encode()).hexdigest()


def request_refresh(account: dict, reason: str) -> None:
    account_id = _account_id(account)
    if not account_id or account_id == "anonymous":
        return
    target = get_account(account_id) or {}
    cookies = str(account.get("cookies") or "")
    fingerprint = _fingerprint(target, cookies)
    with _lock:
        previous = _pending.get(account_id)
        if previous and (previous["status"] == "running" or previous["fingerprint"] == fingerprint):
            if reason not in _PROACTIVE_REASONS:
                previous["reason"] = reason
            return
        _pending[account_id] = {
            "fingerprint": fingerprint, "cookies": cookies, "reason": reason,
            "status": "pending", "attempts": 0, "next_at": 0,
        }
    log(f"steam_cookie_refresh: account={account_id} reason={reason} 已加入后台刷新队列", "info", category="steam")
    wake.set()


def is_history_account_blocked(account: dict) -> bool:
    account_id = _account_id(account)
    with _lock:
        entry = _pending.get(account_id)
        if not entry:
            return False
        if entry["status"] != "running" and entry["cookies"] != str(account.get("cookies") or ""):
            del _pending[account_id]
            return False
        # An early refresh failure does not invalidate a still-usable Cookie.
        return entry["reason"] not in _PROACTIVE_REASONS


def process_pending() -> None:
    from app.pipeline import is_shutdown_pending

    with _lock:
        account_ids = list(_pending)
    for account_id in account_ids:
        if is_shutdown_pending():
            return
        target = get_account(account_id)
        if not target:
            with _lock:
                entry = _pending.get(account_id)
                report = entry and entry["status"] != "manual"
                if entry:
                    entry["status"] = "manual"
            if report:
                log(f"steam_cookie_refresh: account={account_id} 账号不存在，需重新添加账号或移除历史凭证", "warn", category="steam")
            continue
        credentials = steam_auth.get_steam_credentials_for_account(target)
        cookies = str(credentials.get("cookies") or "")
        fingerprint = _fingerprint(target, cookies)
        with _lock:
            entry = _pending.get(account_id)
            if not entry or entry["status"] == "running":
                continue
            # A manual login or credential edit supersedes the failed attempt.
            if entry["fingerprint"] != fingerprint:
                if entry["cookies"] != cookies and not steam_auth.steam_cookie_refresh_reason(credentials):
                    del _pending[account_id]
                    continue
                entry.update(fingerprint=fingerprint, cookies=cookies, status="pending", attempts=0, next_at=0)
            if entry["status"] == "manual" or entry["next_at"] > time.monotonic():
                continue
            entry["status"] = "running"
        try:
            ok, status, message = steam_auth.try_steam_auto_relogin(
                force=True, account_id=account_id, background=True,
            )
        except Exception as exc:
            from utils.diagnostics import format_exception
            ok, status, message = False, "error", format_exception("steam_cookie_refresh", exc)
        with _lock:
            if ok:
                _pending.pop(account_id, None)
            elif status in _MANUAL_STATUSES:
                entry["status"] = "manual"
            else:
                if status not in ("busy", "deferred", "superseded"):
                    entry["attempts"] += 1
                delay = 30 if status in ("busy", "deferred", "superseded") else min(60 * 2 ** min(entry["attempts"] - 1, 6), 3600)
                entry.update(status="retry", next_at=time.monotonic() + delay)
        if ok:
            from app.services import steam_client
            with steam_client._history_pool_lock:
                key = str(target.get("steam_id") or "")
                steam_client._history_pool_cooldowns.pop(key, None)
                steam_client._history_pool_cooldown_status.pop(key, None)
            log(f"steam_cookie_refresh: account={account_id} 刷新成功，恢复行情轮询", "info", category="steam")
        elif status in _MANUAL_STATUSES:
            log(f"steam_cookie_refresh: account={account_id} status={status} {message}；等待修改凭证或手动登录", "warn", category="steam")
        elif status not in ("busy", "deferred", "superseded"):
            log(f"steam_cookie_refresh: account={account_id} status={status} {message}；{delay} 秒后重试", "warn", category="steam")
