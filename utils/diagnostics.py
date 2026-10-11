"""Safe request diagnostics shared by request adapters and task logs."""
import re
import traceback
from pathlib import Path
from urllib.parse import urlsplit


_SECRET_KEYS = {
    "cookie", "cookies", "steamloginsecure", "sessionid", "session_id",
    "token", "access_token", "access-token", "refresh_token", "password",
    "passwd", "secret", "authorization", "proxy-authorization", "shared_secret",
    "csrf_token", "csrftoken", "x-csrftoken",
}
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_SECRET_RE = re.compile(
    r"(?i)\b(cookie|cookies|steamLoginSecure|sessionid|session_id|csrf_token|csrftoken|(?:access[-_]|refresh_)?token|password|passwd|(?:shared_)?secret|authorization)"
    r"\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s;,}\]]+)"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_REASONS = {
    "PROXY_ERROR": "代理连接或认证失败", "PROXY_AUTH_REQUIRED": "代理服务器拒绝认证",
    "TLS_ERROR": "TLS 握手或证书校验失败", "CONNECT_TIMEOUT": "建立连接超时",
    "READ_TIMEOUT": "等待响应超时", "TIMEOUT": "请求超时",
    "CONNECTION_RESET": "连接被对端或中间网络重置", "CONNECTION_REFUSED": "目标或代理端口拒绝连接",
    "DNS_ERROR": "域名解析失败", "CONNECTION_ERROR": "网络连接失败",
    "RATE_LIMITED": "接口返回限流", "AUTH_REFRESH_RATE_LIMITED": "Steam JWT 会话刷新接口返回限流",
    "AUTH_REQUIRED": "接口要求重新认证", "ACCESS_DENIED": "访问被拒绝，需核查账号和网络风控",
    "HTTP_ERROR": "HTTP 响应异常", "INVALID_JSON": "响应无法解析为 JSON",
    "INVALID_RESPONSE": "响应字段或类型不符合接口协议", "API_REJECTED": "接口明确返回失败",
    "EMPTY_HISTORY": "接口请求成功，但没有历史成交记录",
    "HISTORY_STORAGE_ERROR": "历史数据写入数据库失败", "HISTORY_CACHE_READ_ERROR": "读取历史缓存失败",
    "INVALID_HISTORY_POINTS": "历史点的时间或价格字段无效",
    "HISTORY_CURRENCY_UNSUPPORTED": "历史币种缺失或无法换算为人民币",
    "NO_AVAILABLE_PROXY": "强制代理模式没有可用节点", "ACCOUNTS_COOLING_DOWN": "账号处于请求冷却期",
    "HISTORY_ADAPTER_ERROR": "历史请求适配器发生程序错误", "HISTORY_UNAVAILABLE": "调用方未提供失败详情",
    "CANCELLED": "任务被停止", "UNEXPECTED_ERROR": "发生未预期的程序异常",
    "WRITE_RESULT_UNKNOWN": "写请求结果未知，必须核对订单，禁止自动重试",
    "VERIFICATION_REQUIRED": "接口要求安全验证", "REQUEST_BLOCKED": "本地请求策略已阻止请求",
}


def endpoint(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ""))
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.hostname or ''}{port}{parsed.path}" or "unknown"
    except ValueError:
        return "invalid-url"


def redact_text(value, limit: int = 10000) -> str:
    text = str(value if value is not None else "")
    text = _URL_RE.sub(lambda match: "https://" + endpoint(match.group()), text)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s;,\"']+", r"\1 [redacted]", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _JWT_RE.sub("[redacted-token]", text)
    text = " ".join(text.split())
    return text[:limit] + ("... [truncated]" if len(text) > limit else "")


def redact(value):
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if str(key).lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def exception_details(exc: Exception) -> dict:
    chain = []
    pending = [exc]
    seen = set()
    while pending and len(chain) < 8:
        cause = pending.pop(0)
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        row = {"type": type(cause).__name__, "message": redact_text(cause, 1200)}
        cause_frames = traceback.extract_tb(cause.__traceback__)
        if cause_frames:
            frame = cause_frames[-1]
            row["source"] = f"{'/'.join(Path(frame.filename).parts[-3:])}:{frame.lineno}:{frame.name}"
        for name in ("errno", "winerror"):
            if getattr(cause, name, None) is not None:
                row[name] = getattr(cause, name)
        chain.append(row)
        pending.extend(item for item in (
            cause.__cause__, cause.__context__, getattr(cause, "reason", None),
            *getattr(cause, "args", ()),
        ) if isinstance(item, BaseException))
    frames = traceback.extract_tb(exc.__traceback__)
    source = ""
    if frames:
        frame = frames[-1]
        source = f"{'/'.join(Path(frame.filename).parts[-3:])}:{frame.lineno}:{frame.name}"
    stack = [{"file": "/".join(Path(frame.filename).parts[-3:]), "line": frame.lineno, "function": frame.name} for frame in frames[-12:]]
    return {"exception": type(exc).__name__, "detail": redact_text(exc, 1200), "causes": chain, "source": source, "stack": stack}


def classify_exception(exc: Exception) -> str:
    details = exception_details(exc)
    kinds = " ".join(row["type"] for row in details["causes"]).lower()
    message = " ".join(row["message"] for row in details["causes"]).lower()
    if "proxyerror" in kinds:
        if re.search(r"\btunnel connection failed:\s*407\b|\b407\s+proxy authentication required\b", message):
            return "PROXY_AUTH_REQUIRED"
        return "PROXY_ERROR"
    if "sslerror" in kinds or "certificate" in message:
        return "TLS_ERROR"
    if "connecttimeout" in kinds:
        return "CONNECT_TIMEOUT"
    if "readtimeout" in kinds:
        return "READ_TIMEOUT"
    if "timeout" in kinds or "timed out" in message:
        return "TIMEOUT"
    if "10054" in message or "connectionreset" in kinds or "connection reset" in message:
        return "CONNECTION_RESET"
    if "gaierror" in kinds or "nameresolutionerror" in kinds or "getaddrinfo" in message:
        return "DNS_ERROR"
    if "connectionrefused" in kinds or "10061" in message or "connection refused" in message:
        return "CONNECTION_REFUSED"
    if "connectionerror" in kinds or "urlerror" in kinds:
        return "CONNECTION_ERROR"
    return "UNEXPECTED_ERROR"


def http_code(status, auth_refresh=False) -> str:
    if status == 429:
        return "AUTH_REFRESH_RATE_LIMITED" if auth_refresh else "RATE_LIMITED"
    if status in (401, 403):
        return "AUTH_REQUIRED" if status == 401 else "ACCESS_DENIED"
    if status == 407:
        return "PROXY_AUTH_REQUIRED"
    return "HTTP_ERROR"


def one_line(details: dict) -> str:
    fields = [_REASONS.get(details.get("code"), "")] if details.get("code") in _REASONS else []
    for key in (
        "request_id", "code", "stage", "operation", "method", "source", "item", "app_id",
        "account", "attempt", "status", "endpoint", "proxy", "elapsed_ms", "timeout_seconds",
        "auth_refresh_redirected", "retry_after", "content_type", "cookie_present", "cookie_identity_matches", "cookie_expired", "exception", "detail", "action",
    ):
        value = details.get(key)
        if key == "status" and key in details and value is None:
            value = "no_response"
        if value is not None and value != "":
            fields.append(f"{key}={redact_text(value, 1200)}")
    return " ".join(fields)


def format_exception(operation: str, exc: Exception, **context) -> str:
    status = getattr(exc, "code", None)
    code = http_code(status) if isinstance(status, int) and 400 <= status <= 599 else classify_exception(exc)
    return one_line({"operation": operation, "code": code, **context, **exception_details(exc)})
