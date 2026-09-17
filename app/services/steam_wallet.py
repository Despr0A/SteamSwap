"""Steam wallet helpers used for account settlement-currency detection.

This service is intentionally limited to wallet metadata for account-region
checks; unrelated Steam store purchase flows are outside the core scope.
"""

from __future__ import annotations

import json
import re
import time

import requests

from steam.session import parse_cookies


_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_CURRENCY_MAP = {
    1: ("USD", "$"), 2: ("GBP", "£"), 3: ("EUR", "€"), 5: ("RUB", "₽"),
    6: ("PLN", "zł"), 7: ("BRL", "R$"), 8: ("JPY", "¥"), 9: ("NOK", "kr"),
    10: ("IDR", "Rp"), 11: ("MYR", "RM"), 12: ("PHP", "₱"), 13: ("SGD", "S$"),
    14: ("THB", "฿"), 15: ("VND", "₫"), 16: ("KRW", "₩"), 17: ("TRY", "₺"),
    18: ("UAH", "₴"), 19: ("MXN", "Mex$"), 20: ("CAD", "C$"), 21: ("AUD", "A$"),
    22: ("NZD", "NZ$"), 23: ("CNY", "¥"), 24: ("INR", "₹"), 25: ("CLP", "CLP$"),
    26: ("PEN", "S/."), 27: ("COP", "COL$"), 28: ("ZAR", "R"), 29: ("HKD", "HK$"),
    30: ("TWD", "NT$"), 31: ("SAR", "SR"), 32: ("AED", "AED"), 34: ("ARS", "ARS$"),
    35: ("ILS", "₪"), 37: ("KZT", "₸"), 38: ("KWD", "KD"), 39: ("QAR", "QR"),
}
_NO_DIVIDE_CURRENCIES = {8, 15, 16}


def _build_cookies(cookies_raw: str) -> dict:
    cookies = parse_cookies(cookies_raw)
    cookies.setdefault("birthtime", "283993201")
    cookies.setdefault("wants_mature_content", "1")
    return cookies


def _normalize_country_code(value) -> str:
    value = str(value or "").strip().upper()
    return value if re.fullmatch(r"[A-Z]{2}", value) else ""


def _find_country_code(value) -> str:
    if isinstance(value, dict):
        for key in ("country_code", "country", "store_country_code", "wallet_country", "user_country"):
            code = _normalize_country_code(value.get(key))
            if code:
                return code
        for child in value.values():
            code = _find_country_code(child)
            if code:
                return code
    elif isinstance(value, list):
        for child in value:
            code = _find_country_code(child)
            if code:
                return code
    return ""


def get_base_auth_status(cookies_raw: str, *, require_country: bool = False):
    """Read Steam store metadata needed to obtain a wallet API token."""
    url = f"https://store.steampowered.com/cart/?_t={int(time.time() * 1000)}"
    cookies = _build_cookies(cookies_raw)
    from utils.proxy_manager import get_proxy_manager

    try:
        proxies = get_proxy_manager().get_proxies_for_request()
        response = requests.get(
            url, cookies=cookies, headers=_HEADERS, proxies=proxies,
            verify=False, timeout=15,
        )
        text = response.text or ""
        config_match = re.search(
            r'data-store_user_config=["\']([^"\']+)["\']', text
        )
        config = {}
        if config_match:
            try:
                import html
                config = json.loads(html.unescape(config_match.group(1)))
            except Exception:
                config = {}
        token = config.get("webapi_token", "") if isinstance(config, dict) else ""
        country = _find_country_code(config)
        if not country:
            for key in ("country_code", "country", "store_country_code", "wallet_country"):
                match = re.search(rf'"{key}"\s*:\s*"([A-Za-z]{{2}})"', text)
                if match:
                    country = _normalize_country_code(match.group(1))
                    if country:
                        break
        if require_country and not country:
            raise RuntimeError("无法从 Steam 商店页面解析账号地区")
        return token, country or "CN", config
    except Exception as exc:
        raise RuntimeError(f"获取底层鉴权失败: {exc}") from exc


def _wallet_result(balance_raw: int, delayed_raw: int, currency_id: int, wallet_country: str) -> dict:
    currency_info = _CURRENCY_MAP.get(currency_id)
    if not currency_info:
        raise RuntimeError(f"未知 Steam 钱包币种 ID: {currency_id}")
    code, symbol = currency_info
    total_raw = balance_raw + delayed_raw
    display = (
        f"{symbol}{total_raw:,}"
        if currency_id in _NO_DIVIDE_CURRENCIES
        else f"{symbol}{total_raw / 100.0:.2f}"
    )
    country = _normalize_country_code(wallet_country)
    return {
        "balance_raw": total_raw,
        "balance_display": display,
        "currency_code": code,
        "currency_symbol": symbol,
        "currency_id": currency_id,
        "wallet_country": country,
        "country_code": country,
    }


def get_wallet_balance(cookies_raw: str) -> dict:
    """Return Steam wallet balance and settlement currency metadata."""
    cookies = _build_cookies(cookies_raw)
    from utils.proxy_manager import get_proxy_manager

    proxies = get_proxy_manager().get_proxies_for_request()
    unknown_currency_id = None
    try:
        response = requests.get(
            "https://steamcommunity.com/market/",
            cookies=cookies,
            headers={**_HEADERS, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            proxies=proxies, verify=False, timeout=15,
        )
        match = re.search(r"g_rgWalletInfo\s*=\s*(\{[^;]+\})", response.text or "")
        if match:
            wallet = json.loads(match.group(1))
            if wallet.get("success") == 1 or "wallet_balance" in wallet:
                try:
                    return _wallet_result(
                        int(wallet.get("wallet_balance", 0)),
                        int(wallet.get("wallet_delayed_balance", 0)),
                        int(wallet.get("wallet_currency", 0)),
                        wallet.get("wallet_country", ""),
                    )
                except RuntimeError:
                    unknown_currency_id = int(wallet.get("wallet_currency", 0) or 0)
    except Exception:
        pass

    try:
        token, _, _ = get_base_auth_status(cookies_raw)
        if token:
            response = requests.get(
                "https://api.steampowered.com/IWalletService/GetWalletDetails/v1",
                params={"access_token": token}, headers=_HEADERS,
                proxies=proxies, verify=False, timeout=15,
            )
            if response.status_code == 200:
                data = response.json().get("response", {})
                try:
                    return _wallet_result(
                        int(data.get("balance", 0)),
                        int(data.get("delayed_balance", 0)),
                        int(data.get("currency", 0)),
                        data.get("country") or data.get("wallet_country") or data.get("country_code", ""),
                    )
                except RuntimeError:
                    unknown_currency_id = int(data.get("currency", 0) or 0)
    except Exception:
        pass
    if unknown_currency_id:
        raise RuntimeError(f"未知 Steam 钱包币种 ID: {unknown_currency_id}")
    raise RuntimeError("无法获取 Steam 钱包余额，请确认 Cookie 有效且账户已设置钱包")
