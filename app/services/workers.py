"""
Background worker threads – extracted from api.py.
Contains receive, exchange rate,
and account region sync workers.
"""
import json
import time
from typing import Optional
from utils.diagnostics import format_exception
from app.state import (
    get_status,
    get_purchases,
    is_steam_background_allowed,
    log,
    set_buff_auth_expired,
    set_buff_verification_required,
    update_purchase,
    update_purchase_by_id,
)
from app.config_loader import (
    get_buff_credentials,
    get_steam_credentials,
    load_app_config_validated,
)
from app.notify import send_pushplus
from app.inventory_cs2 import scan_cs2_inventory
from app.accounts import get_current_account, list_accounts, update_account
_worker_alert_last: dict = {}  
_WORKER_ALERT_COOLDOWN = 3600  
def _worker_alert(worker_name: str, error: Exception) -> None:
    """发送 PushPlus 告警，每个 worker 每小时至多发一次。"""
    now = time.time()
    last = _worker_alert_last.get(worker_name, 0.0)
    if now - last < _WORKER_ALERT_COOLDOWN:
        return
    try:
        cfg = load_app_config_validated()
        token = (cfg.get("notify") or {}).get("pushplus_token", "") or ""
        if not token:
            return
        msg = str(error)[:200] if error else "未知异常"
        send_pushplus(token, f"[Worker异常] {worker_name}", f"后台任务 <b>{worker_name}</b> 发生异常，已自动重试。<br>错误信息：{msg}")
        _worker_alert_last[worker_name] = now
        log(f"[{worker_name}] 异常告警已发送", "info", category="alert")
    except Exception:
        pass
def _fetch_exchange_rates(base: str = "CNY", targets: Optional[list] = None) -> Optional[dict]:
    try:
        import requests
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        proxies = pm.get_proxies_for_request()  
        url = f"https://open.er-api.com/v6/latest/{base}"
        from app.state import log
        if proxies:
            log(f"exchange_rate: 正在使用代理 {proxies.get('http')} 访问: {url}", "debug", category="exchange_rate")
        r = requests.get(url, timeout=10, proxies=proxies)
        data = r.json()
        if data.get("result") != "success":
            return None
        all_rates = data.get("rates") or {}
        if not isinstance(all_rates, dict):
            return None
        # The endpoint returns foreign-currency units per CNY.  Store the
        # inverse so callers can multiply a source-currency amount by the
        # value to obtain CNY.  Saving the complete snapshot avoids making
        # account-wallet metadata a prerequisite for history conversion.
        selected = all_rates if not targets else {
            str(code).upper(): all_rates.get(code)
            for code in targets
        }
        out = {}
        out[base.upper()] = 1.0
        converted_count = 0
        for code, rate_val in selected.items():
            if not rate_val:
                continue
            try:
                rate = 1.0 / float(rate_val)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if rate > 0:
                out[str(code).upper()] = rate
                converted_count += 1
        return out if converted_count else None
    except Exception:
        return None
def _save_exchange_rates(rates: dict, base: str = "CNY") -> None:
    try:
        from datetime import datetime, timezone
        _EXCHANGE_RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base": base,
            "rates": rates,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(_EXCHANGE_RATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        return
def exchange_rate_worker() -> None:
    while True:
        try:
            cfg = load_app_config_validated()
            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("exchange_rate_refresh_hours") or 0)
            if interval_h <= 0:
                log("exchange_rate: 已关闭, system.exchange_rate_refresh_hours<=0", "debug", category="exchange_rate")
                time.sleep(3600)
                continue
            log(f"exchange_rate: 开始获取, 间隔={interval_h} 小时, 基准=CNY", "debug", category="exchange_rate")
            rates = _fetch_exchange_rates("CNY")
            if rates is not None:
                _save_exchange_rates(rates, "CNY")
                preview = ", ".join(f"{k}={v:.4f}" for k, v in list(rates.items())[:12])
                if len(rates) > 12:
                    preview += ", …"
                log(f"exchange_rate: 已更新 {len(rates)} 个币种: {preview}", "debug", category="exchange_rate")
                sleep_seconds = max(1, int(interval_h * 3600))
            else:
                log("exchange_rate: 获取失败或无有效结果", "error", category="exchange_rate")
                # Retry a failed initial/refresh request soon; a successful
                # snapshot still refreshes only at the configured daily cadence.
                sleep_seconds = 300
            time.sleep(sleep_seconds)
        except Exception as e:
            log(f"exchange_rate: worker 异常 {format_exception('exchange_rate_worker', e)}, 5 分钟后重试", "error", category="exchange_rate")
            time.sleep(300)
_BUFF_RECONCILIATION_REQUIRED_STEPS = frozenset(
    {
        "BUFF_WRITE_RESULT_UNKNOWN",
        "BUFF_ORDER_CREATED_PENDING",
        "BUFF_COOLING_DOWN",
        "PIPELINE_UNEXPECTED_ERROR",
    }
)


def _buff_background_request_is_safe() -> bool:
    """BUFF background reads must never overlap a buy pipeline or checkout."""

    from app.pipeline import is_shutdown_pending
    from app.services.buff_checkout_guard import get_unresolved_checkout

    if is_shutdown_pending() or get_unresolved_checkout() is not None:
        return False
    status = get_status() or {}
    return (
        status.get("status") != "running"
        and status.get("step") != "CHECKOUT_PENDING"
        and status.get("step") not in _BUFF_RECONCILIATION_REQUIRED_STEPS
        and not status.get("buff_auth_expired")
        and not status.get("buff_verification_required")
    )


def receive_worker() -> None:
    from app.receive_flow import try_receive_once
    from app.services.buff_auth import get_buff_auth_lock
    from app.services.buff_client import create_buff_client_from_config
    from app.services.buff_checkout_guard import buff_activity_guard
    from buff import (
        BuffAuthExpired,
        BuffRateLimited,
        BuffRequestBlocked,
        BuffVerificationRequired,
    )

    buff_client = None
    while True:
        try:
            cfg = load_app_config_validated()
            interval = max(10, int(cfg.get("pipeline", {}).get("receive_poll_interval_seconds", 30) or 30))
            time.sleep(interval)
            if not is_steam_background_allowed() or not _buff_background_request_is_safe():
                continue
            # Hold the complete receive transaction (BUFF task lookup, Steam
            # offer acceptance and DB update) outside pipeline start/import/
            # data reset.  The inner BuffClient calls are re-entrant.
            with get_buff_auth_lock():
                with buff_activity_guard():
                    if (
                        not is_steam_background_allowed()
                        or not _buff_background_request_is_safe()
                    ):
                        continue
                    purchases = get_purchases()
                    if not any(
                        p.get("pending_receipt") and not p.get("assetid")
                        for p in purchases
                    ):
                        continue
                    credentials = get_buff_credentials() or {}
                    if not credentials.get("cookies"):
                        continue
                    if buff_client is None:
                        buff_client = create_buff_client_from_config(
                            credentials,
                            cfg,
                        )
                    n = try_receive_once(
                        get_purchases,
                        update_purchase,
                        lambda: buff_client,
                        get_steam_credentials,
                        scan_inventory=scan_cs2_inventory,
                        update_purchase_by_id=update_purchase_by_id,
                    )
            if n > 0:
                log(f"receive_worker: 本轮收取到 {n} 件物品", "info", category="receive")
        except BuffRateLimited as e:
            log(f"receive_worker: Buff 请求处于限流冷却: {e}", "warn", category="receive")
            time.sleep(min(60, max(1, int(e.retry_after))))
        except BuffVerificationRequired as e:
            set_buff_verification_required(True, str(e))
            log(f"receive_worker: Buff 需要安全验证: {e}", "warn", category="receive")
            time.sleep(60)
        except BuffAuthExpired:
            set_buff_auth_expired(True)
            log("receive_worker: Buff 登录已失效，暂停待收货查询", "warn", category="receive")
            time.sleep(60)
        except BuffRequestBlocked as e:
            log(f"receive_worker: Buff 请求策略已阻止后台查询: {e}", "warn", category="receive")
            time.sleep(60)
        except Exception as e:
            log(f"receive_worker 异常 {format_exception('receive_worker', e)}", "error", category="receive")
            _worker_alert("receive_worker", e)
            time.sleep(60)
def _sync_account_profile_and_region(acc: dict) -> None:
    from app.services.steam_auth import fetch_steam_profile_via_api
    account_id = acc.get("id")
    cred = get_steam_credentials()
    cred_steam_id = (cred.get("steam_id") or "").strip()
    acc_steam_id = (acc.get("steam_id") or "").strip()
    cookies_str = cred.get("cookies") or ""
    if cred_steam_id and not acc_steam_id:
        update_account(account_id, steam_id=cred_steam_id)
        acc_steam_id = cred_steam_id
        log(f"account_sync: 从 credentials 同步 steam_id={cred_steam_id}", "info", category="account")
    steam_id = acc_steam_id or cred_steam_id
    if steam_id and cookies_str and (not acc.get("display_name") or not acc.get("avatar_url")):
        display_name, avatar_url = fetch_steam_profile_via_api(steam_id, cookies_str)
        if display_name or avatar_url:
            updates = {}
            if display_name and not acc.get("display_name"):
                updates["display_name"] = display_name
            if avatar_url and not acc.get("avatar_url"):
                updates["avatar_url"] = avatar_url
            if updates:
                update_account(account_id, **updates)
                log(f"account_sync: 已获取 Steam 资料 name={display_name or '(无)'} avatar={'(有)' if avatar_url else '(无)'}", "info", category="account")
        else:
            log("account_sync: 未能获取 Steam 资料（网络异常或 Cookie 失效）", "debug", category="account")

def sync_account_region_worker() -> None:
    try:
        acc = get_current_account()
        if not acc:
            log("account_region: 无当前账号，跳过同步", "debug", category="account")
            return
        log(
            f"account_region: 开始同步 account_id={acc.get('id')} username={acc.get('username') or ''} steam_id={acc.get('steam_id') or ''}",
            "debug",
            category="account",
        )
        _sync_account_profile_and_region(acc)
        
        from app.services.account_region import refresh_account_region_currency
        # Startup only refreshes the account profile/Cookie.  A wallet is not
        # required for history reads because those prices are normalized with
        # the daily exchange-rate snapshot.
        result = refresh_account_region_currency(
            acc.get("id"),
            skip_unconfigured=True,
            require_wallet=False,
        )
        if not result.get("ok"):
            if result.get("skipped"):
                log(
                    f"account_region: {result.get('error') or '尚未完成 Steam 登录配置'}",
                    "debug",
                    category="account",
                )
                return
            log(
                f"account_region: 启动资料同步失败: {result.get('error') or '未知原因'}",
                "error",
                category="account",
            )
            return
        log(
            f"account_region: 同步完成 account_id={acc.get('id')} "
            f"币种检查={result.get('status') or 'skipped'}",
            "debug",
            category="account",
        )
    except Exception as e:
        log(f"account_region: 同步异常 {format_exception('account_region', e)}", "error", category="account")
        return
def _session_keepalive_enabled(cfg: dict) -> bool:
    return bool((cfg.get("system") or {}).get("buff_session_keepalive_enabled", False))


def _session_keepalive_is_safe() -> bool:
    """Never open an auth browser while a pipeline or checkout is active."""

    from app.pipeline import is_shutdown_pending
    from app.services.buff_checkout_guard import get_unresolved_checkout

    if is_shutdown_pending() or get_unresolved_checkout() is not None:
        return False
    status = get_status() or {}
    return (
        status.get("status") != "running"
        and status.get("step") != "CHECKOUT_PENDING"
        and status.get("step") not in _BUFF_RECONCILIATION_REQUIRED_STEPS
    )


def _keepalive_sessions() -> None:
    from app.services.buff_auth import try_buff_auto_relogin
    from app.services.steam_auth import try_steam_auto_relogin

    for service, relogin in (("Steam", try_steam_auto_relogin), ("Buff", try_buff_auto_relogin)):
        log(f"keepalive: 开始本轮 {service} 后台会话保活...", "info", category="keepalive")
        ok, _status, message = relogin()
        log(
            f"keepalive: {service} 保活{'成功' if ok else '未完成'}: {message}",
            "info" if ok else "warn", category="keepalive",
        )


def _refresh_due_steam_cookies() -> None:
    from app.services import steam_auth, steam_refresh

    for account in list_accounts():
        account_id = account.get("id")
        if not account_id:
            continue
        credentials = steam_auth.get_steam_credentials_for_account(account)
        reason = steam_auth.steam_cookie_refresh_reason(credentials)
        expected = str(account.get("steam_id") or "").strip()
        if expected and steam_auth.cookie_metadata(credentials.get("cookies", "")).get("steam_id") != expected:
            reason = "identity_mismatch"
        if reason:
            steam_refresh.request_refresh({**account, "cookies": credentials.get("cookies", "")}, reason)
    steam_refresh.process_pending()


def steam_cookie_refresh_worker() -> None:
    from app.services.steam_refresh import wake

    while True:
        wake.clear()
        try:
            _refresh_due_steam_cookies()
        except Exception as exc:
            log(format_exception("steam_cookie_refresh_worker", exc), "error", category="steam")
        wake.wait(30)


def session_keepalive_worker() -> None:

    while True:
        try:
            cfg = load_app_config_validated()
            if not _session_keepalive_enabled(cfg):
                # Poll the opt-in switch without touching either remote service.
                time.sleep(60)
                continue

            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("session_keepalive_hours", 4.0))
            if interval_h <= 0:
                log(
                    "keepalive: 已开启但 system.session_keepalive_hours<=0，等待配置修正",
                    "debug",
                    category="keepalive",
                )
                time.sleep(60)
                continue

            # The first request waits the complete configured interval; startup
            # no longer forces a browser visit after five minutes.
            time.sleep(interval_h * 3600)

            cfg = load_app_config_validated()
            if not _session_keepalive_enabled(cfg):
                continue
            while not _session_keepalive_is_safe():
                log("keepalive: 流水线或结账正在进行，延后会话保活", "debug", category="keepalive")
                time.sleep(60)
                cfg = load_app_config_validated()
                if not _session_keepalive_enabled(cfg):
                    break
            if not _session_keepalive_enabled(cfg):
                continue

            # Re-check immediately before launching the browser to close the
            # race between the wait loop and a newly started pipeline.
            if not _session_keepalive_is_safe():
                continue
            _keepalive_sessions()
        except Exception as e:
            log(f"keepalive: worker 异常 {e}, 15 分钟后重试", "error", category="keepalive")
            time.sleep(900)
