import base64
import imaplib
import email
import os
import shutil
import subprocess
import sys
import threading
import time
from email.header import decode_header
from urllib.parse import quote
from typing import Callable, Optional
from pathlib import Path
import requests


WINDOWS_APP_USER_MODEL_ID = "AetherSwap"
WINDOWS_NOTIFICATION_LAUNCH_URI = "http://127.0.0.1:28472/"
_windows_registration_lock = threading.Lock()
_windows_registration_result: Optional[bool] = None


def _register_windows_aumid() -> bool:
    """Register the AUMID on a Start Menu shortcut for an unpackaged app.

    Windows requires an unpackaged desktop process to have a Start Menu
    shortcut whose ``System.AppUserModel.ID`` matches the ID passed to
    ``CreateToastNotifier``.  Registration is best effort so the notification
    path still works for packaged installs and environments with no pywin32.
    """
    global _windows_registration_result
    if _windows_registration_result is not None:
        return _windows_registration_result
    with _windows_registration_lock:
        if _windows_registration_result is not None:
            return _windows_registration_result
        try:
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise RuntimeError("APPDATA 未设置")
            from win32com.client import Dispatch
            from win32com.propsys import propsys, pscon
            from win32com.shell import shellcon

            shortcut_dir = (
                Path(appdata)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
            )
            shortcut_dir.mkdir(parents=True, exist_ok=True)
            shortcut_path = shortcut_dir / f"{WINDOWS_APP_USER_MODEL_ID}.lnk"
            shortcut = Dispatch("WScript.Shell").CreateShortcut(str(shortcut_path))
            target = Path(sys.executable).resolve()
            shortcut.TargetPath = str(target)
            argv = list(sys.argv)
            if argv and Path(argv[0]).exists():
                shortcut.Arguments = subprocess.list2cmdline(argv[0:])
            shortcut.WorkingDirectory = str(Path.cwd())
            shortcut.Description = "AetherSwap"
            shortcut.IconLocation = f"{target},0"
            shortcut.Save()

            store = propsys.SHGetPropertyStoreFromParsingName(
                str(shortcut_path), None, shellcon.GPS_READWRITE
            )
            store.SetValue(
                pscon.PKEY_AppUserModel_ID,
                propsys.PROPVARIANT(WINDOWS_APP_USER_MODEL_ID),
            )
            store.Commit()
            _windows_registration_result = True
        except Exception:
            _windows_registration_result = False
        return _windows_registration_result


def _send_winrt_toast(title: str, message: str) -> bool | None:
    """Send a toast through the Windows Runtime projection when installed.

    ``None`` means the optional WinRT projection is not installed, allowing the
    PowerShell compatibility path to be attempted.
    """
    try:
        from html import escape
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import (
            ToastNotification,
            ToastNotificationManager,
        )
    except ImportError:
        return None

    try:
        xml = XmlDocument()
        xml.load_xml(
            "<toast launch=\"{launch}\"><visual><binding template=\"ToastGeneric\">"
            "<text>{title}</text><text>{message}</text>"
            "</binding></visual></toast>".format(
                launch=escape(WINDOWS_NOTIFICATION_LAUNCH_URI, quote=True),
                title=escape(str(title or "AetherSwap")),
                message=escape(str(message or "")),
            )
        )
        notifier = ToastNotificationManager.create_toast_notifier_with_id(
            WINDOWS_APP_USER_MODEL_ID
        )
        notifier.show(ToastNotification(xml))
        return True
    except Exception:
        # An unpackaged interpreter can lack an AUMID registration even when
        # WinRT is installed; let the compatibility route try in that case.
        return False


def send_windows_toast(title: str, message: str) -> bool:
    """Display a Windows notification-center App notification.

    The WinRT path is the primary implementation.  PowerShell remains as a
    best-effort fallback for existing installations that have not installed the
    optional projection package yet.
    """
    if sys.platform != "win32":
        return False

    _register_windows_aumid()
    winrt_result = _send_winrt_toast(title, message)
    if winrt_result is True:
        return True

    powershell = next(
        (
            executable
            for executable in (
                shutil.which("powershell.exe"),
                shutil.which("powershell"),
                shutil.which("pwsh.exe"),
                shutil.which("pwsh"),
            )
            if executable
        ),
        "",
    )
    if not powershell:
        return False

    title_b64 = base64.b64encode(str(title or "AetherSwap").encode("utf-8")).decode("ascii")
    message_b64 = base64.b64encode(str(message or "").encode("utf-8")).decode("ascii")
    script = f'''$ErrorActionPreference = "Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{title_b64}"))
$message = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{message_b64}"))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast launch='http://127.0.0.1:28472/'><visual><binding template='ToastGeneric'><text></text><text></text></binding></visual></toast>")
$textNodes = $xml.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null
$textNodes.Item(1).AppendChild($xml.CreateTextNode($message)) | Out-Null
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AetherSwap").Show($toast)
'''
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded_script,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def send_pushplus(token: str, title: str, content: str, template: str = "html") -> bool:
    if not token or not token.strip():
        return False
    try:
        r = requests.post(
            "http://www.pushplus.plus/send",
            json={"token": token.strip(), "title": title, "content": content, "template": template},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False
_last_manual_notify_time: dict = {}
_last_manual_notify_lock = threading.Lock()  
def notify_manual_intervention_required(platform: str, reason: str) -> bool:
    """
    Sends a PushPlus notification when manual intervention is required (e.g., login expired).
    Includes rate-limiting per platform (max 1 notify per 4 hours).
    """
    from app.config_loader import load_app_config_validated
    now = time.time()
    with _last_manual_notify_lock:
        last_time = _last_manual_notify_time.get(platform, 0)
        if now - last_time < 14400:
            return False
        _last_manual_notify_time[platform] = now
    cfg = load_app_config_validated()
    notify_cfg = cfg.get("notify") or {}
    token = (notify_cfg.get("pushplus_token") or "").strip()
    if not token:
        with _last_manual_notify_lock:
            _last_manual_notify_time[platform] = last_time
        return False
    title = f"[自动挂刀报警] {platform} 平台需要手动介入"
    content = (
        f"<b>报警平台:</b> {platform}<br/><br/>"
        f"<b>原因说明:</b><br/>"
        f"<span style='color: red;'>{reason}</span><br/><br/>"
        f"为了避免被限制或错失交易，请尽快前往图形界面或浏览器完成手动登录与验证操作。"
    )
    success = send_pushplus(token, title, content)
    if not success:
        with _last_manual_notify_lock:
            _last_manual_notify_time[platform] = last_time
    return success
def build_payment_notify_content(
    name: str,
    price: float,
    pay_url: str,
    pay_type: str,
    acc: float,
    sell_ratio: Optional[float] = None,
    num: int = 1,
    value_ratio: Optional[float] = None,
    steam_market_hash_name: Optional[str] = None,
    steam_app_id: int = 730,
    steam_link: Optional[str] = None,
) -> str:
    ratio_str = ""
    if value_ratio is not None:
        ratio_str = f"<br/>与最高折扣对比: {value_ratio:.4f}"
    elif sell_ratio is not None:
        ratio_str = f"<br/>最优寄售: {sell_ratio:.2%}"
    total = price * num
    pay_label = "支付宝" if (pay_type or "").lower() in ("alipay", "支付宝") else "微信"
    lines = [
        f"<b>物品</b>: {name}<br/>",
        f"<b>购买单价</b>: {price:.2f} 元<br/>",
        f"<b>数量</b>: {num}<br/>",
        f"<b>总价</b>: {total:.2f} 元{ratio_str}<br/>",
        f"<b>当前累计</b>: {acc + total:.2f} 元<br/>",
        f"<b>付款方式</b>: {pay_label}<br/>",
        f"<a href=\"{pay_url}\">点击付款链接</a>",
    ]
    if steam_link and (steam_link := steam_link.strip()):
        lines.append(f"<br/><a href=\"{steam_link}\">Steam 市场链接</a>")
    elif steam_market_hash_name and (steam_market_hash_name := steam_market_hash_name.strip()):
        market_url = f"https://steamcommunity.com/market/listings/{steam_app_id}/{quote(steam_market_hash_name, safe='')}"
        lines.append(f"<br/><a href=\"{market_url}\">Steam 市场链接</a>")
    return "".join(lines)
def wait_email_command(
    cfg: dict,
    timeout_seconds: int = 300,
    is_stop_requested: Optional[Callable[[], bool]] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> str:
    n = cfg.get("notify") or {}
    user = (n.get("email_user") or "").strip()
    passwd = (n.get("email_pass") or "").strip()
    server = (n.get("imap_server") or "imap.qq.com").strip()
    target_sender = (n.get("target_sender") or "").strip()
    subject_success = (n.get("subject_success") or "已确认成功付款").strip()
    subject_fail = (n.get("subject_fail") or "已确认付款失败").strip()
    allowed_sender = (n.get("allowed_sender") or "").strip()
    if not user or not passwd:
        return "timeout"
    start = time.time()
    timeout = min(timeout_seconds, 86400)
    if log_fn:
        log_fn(f"监听邮箱指令 发件人含「{target_sender}」 成功标题「{subject_success}」 失败标题「{subject_fail}」", "info")
    while time.time() - start < timeout:
        if is_stop_requested and is_stop_requested():
            return "timeout"
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server)
            mail.login(user, passwd)
            mail.select("inbox")
            status, messages = mail.search(None, "UNSEEN")
            email_ids = (messages or [b""])[0].split()
            if not email_ids:
                time.sleep(3)
                continue
            latest_id = email_ids[-1]
            _, msg_data = mail.fetch(latest_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender_header = msg.get("From", "")
            sender_name, sender_addr = email.utils.parseaddr(sender_header)
            full_sender = f"{sender_name} {sender_addr}"
            subject_parts = decode_header(msg.get("Subject", ""))
            parts = []
            for part, encoding in subject_parts:
                if isinstance(part, bytes):
                    enc = (encoding or "utf-8").strip().lower()
                    if enc in ("unknown-8bit", "unknown", ""):
                        enc = "utf-8"
                    try:
                        parts.append(part.decode(enc, errors="replace"))
                    except LookupError:
                        parts.append(part.decode("utf-8", errors="replace"))
                else:
                    parts.append(str(part))
            subject_str = "".join(parts)
            if log_fn:
                log_fn(f"收到邮件 发件人={full_sender[:50]} 标题={subject_str[:40]}", "info")
            is_sender_ok = target_sender.lower() in full_sender.lower() if target_sender else True
            if allowed_sender and sender_addr:
                is_sender_ok = is_sender_ok and (allowed_sender.lower() in (sender_addr or "").lower())
            if not is_sender_ok:
                mail.store(latest_id, "+FLAGS", "\\Seen")
                mail.expunge()
                time.sleep(3)
                continue
            if subject_success and subject_success in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认成功付款", "info")
                return "success"
            if subject_fail and subject_fail in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认付款失败", "info")
                return "fail"
            mail.store(latest_id, "+FLAGS", "\\Seen")
            mail.expunge()
        except Exception as e:
            if log_fn:
                log_fn(f"邮箱连接/解析异常: {e}", "warn")
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass
            mail = None
        time.sleep(3)
    if log_fn:
        log_fn("邮箱等待超时", "warn")
    return "timeout"
