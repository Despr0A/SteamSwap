import json
import random
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from utils.diagnostics import endpoint, format_exception, redact_text

from .models import SteamDTQueryParams, SteamDTRow
from .parser import parse_response

API_URL = "https://www.steamdt.com/api/user/ranking/v1/hanging-knife"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/116.0.5845.97 Safari/537.36"
)


def _make_timestamp() -> str:
    raw = str(int(time.time() * 1000))
    truncated = raw[:12]
    digit_sum = sum(int(d) for d in truncated)
    check_digit = digit_sum % 10
    return truncated + str(check_digit)


def fetch_steamdt_data(
    params: Optional[SteamDTQueryParams] = None,
    *,
    timeout: float = 12,
    attempts: int = 3,
    retry_delay: float = 0.8,
    jitter: float = 0.4,
    target_platform: str = "BUFF",
    verbose: bool = False,
) -> List[SteamDTRow]:
    p = params or SteamDTQueryParams()
    result: Dict[str, Any] = {}

    for attempt in range(1, attempts + 1):
        timestamp = _make_timestamp()
        payload = {**p.to_payload(), "timestamp": timestamp}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Language": p.language,
            "X-App-Version": "1.0.0",
            "X-Currency": p.currency,
            "X-Device": "1",
            "Origin": "https://www.steamdt.com",
            "Referer": "https://www.steamdt.com/hanging",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": DEFAULT_USER_AGENT,
            "Access-Token": "undefined",
        }
        req = Request(
            f"{API_URL}?timestamp={timestamp}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                result = json.loads(resp.read().decode(charset))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as e:
            raise RuntimeError(f"SteamDT 请求失败: {format_exception('steamdt.ranking', e, endpoint=endpoint(API_URL), method='POST', attempt=f'{attempt}/{attempts}', timeout_seconds=timeout, status=getattr(e, 'code', None))}") from e

        if not isinstance(result, dict):
            raise RuntimeError("SteamDT 接口返回格式异常：预期 JSON 对象")

        if result.get("success") or attempt == attempts:
            break

        error_code = result.get("errorCode")
        if error_code not in {100, 108}:
            break

        sleep_sec = (retry_delay * attempt) + random.uniform(0, jitter)
        if verbose:
            print(f"  attempt {attempt} errorCode={error_code}, retrying in {sleep_sec:.1f}s...")
        time.sleep(sleep_sec)

    if not result.get("success"):
        detail = result.get("message") or result.get("errorMsg") or result.get("error") or "无错误详情"
        raise RuntimeError(f"SteamDT 接口失败: operation=steamdt.ranking endpoint={endpoint(API_URL)} stage=validate attempt={attempt}/{attempts} errorCode={result.get('errorCode')}，{redact_text(detail, 1200)}")
    return parse_response(result, target_platform=target_platform)
