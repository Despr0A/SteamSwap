import base64
import time

import pytest


def test_windows_toast_is_a_noop_outside_windows(monkeypatch):
    from app import notify

    monkeypatch.setattr(notify.sys, "platform", "linux")

    assert notify.send_windows_toast("Title", "Message") is False


def test_windows_toast_launches_hidden_powershell(monkeypatch):
    from app import notify

    calls = []
    monkeypatch.setattr(notify, "_windows_registration_result", False)
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(
        notify.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or type("Result", (), {"returncode": 0})(),
    )

    assert notify.send_windows_toast("SteamSwap", "待付款") is True

    command, kwargs = calls[0]
    assert command[:5] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"]
    assert "-EncodedCommand" in command
    script = base64.b64decode(command[-1]).decode("utf-16le")
    assert "ToastNotificationManager" in script
    assert 'CreateToastNotifier("SteamSwap")' in script
    assert "CreateTextNode($title)" in script
    assert "CreateTextNode($message)" in script
    assert kwargs["stdout"] is notify.subprocess.DEVNULL
    assert kwargs["timeout"] == 5


def test_winrt_toast_uses_aumid_and_launch_uri(monkeypatch):
    from app import notify
    import types
    import sys

    calls = []

    class XmlDocument:
        def load_xml(self, value):
            calls.append(("xml", value))

    class ToastNotification:
        def __init__(self, xml):
            calls.append(("toast", xml))

    class Notifier:
        def show(self, toast):
            calls.append(("show", toast))

    class Manager:
        @classmethod
        def create_toast_notifier_with_id(cls, value):
            calls.append(("aumid", value))
            return Notifier()

    modules = {
        "winrt": types.ModuleType("winrt"),
        "winrt.windows": types.ModuleType("winrt.windows"),
        "winrt.windows.data": types.ModuleType("winrt.windows.data"),
        "winrt.windows.data.xml": types.ModuleType("winrt.windows.data.xml"),
        "winrt.windows.data.xml.dom": types.ModuleType("winrt.windows.data.xml.dom"),
        "winrt.windows.ui": types.ModuleType("winrt.windows.ui"),
        "winrt.windows.ui.notifications": types.ModuleType("winrt.windows.ui.notifications"),
    }
    modules["winrt.windows.data.xml.dom"].XmlDocument = XmlDocument
    modules["winrt.windows.ui.notifications"].ToastNotification = ToastNotification
    modules["winrt.windows.ui.notifications"].ToastNotificationManager = Manager
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify, "_windows_registration_result", False)

    assert notify.send_windows_toast("标题", "订单待付款") is True
    assert ("aumid", "SteamSwap") in calls
    assert "http://127.0.0.1:28472/" in calls[0][1]
    assert "订单待付款" in calls[0][1]


def test_unpacked_windows_app_registers_aumid_shortcut(monkeypatch, tmp_path):
    from app import notify
    import types
    import sys

    shortcut_calls = []
    property_calls = []

    class Shortcut:
        def __setattr__(self, key, value):
            if key != "_values":
                shortcut_calls.append((key, value))
            object.__setattr__(self, key, value)

        def Save(self):
            shortcut_calls.append(("Save",))

    class Shell:
        def CreateShortcut(self, path):
            shortcut_calls.append(("CreateShortcut", path))
            return Shortcut()

    class Store:
        def SetValue(self, key, value):
            property_calls.append(("SetValue", key, value))

        def Commit(self):
            property_calls.append(("Commit",))

    fake_client = types.ModuleType("win32com.client")
    fake_client.Dispatch = lambda name: Shell()
    fake_propsys = types.ModuleType("win32com.propsys.propsys")
    fake_propsys.SHGetPropertyStoreFromParsingName = lambda *args: Store()
    fake_propsys.PROPVARIANT = lambda value: ("PROPVARIANT", value)
    fake_pscon = types.ModuleType("win32com.propsys.pscon")
    fake_pscon.PKEY_AppUserModel_ID = "PKEY_AppUserModel_ID"
    fake_shellcon = types.ModuleType("win32com.shell.shellcon")
    fake_shellcon.GPS_READWRITE = 2
    fake_propsys_pkg = types.ModuleType("win32com.propsys")
    fake_propsys_pkg.__path__ = []
    fake_propsys_pkg.propsys = fake_propsys
    fake_propsys_pkg.pscon = fake_pscon
    fake_shell_pkg = types.ModuleType("win32com.shell")
    fake_shell_pkg.__path__ = []
    fake_shell_pkg.shellcon = fake_shellcon
    modules = {
        "win32com": types.ModuleType("win32com"),
        "win32com.client": fake_client,
        "win32com.propsys": fake_propsys_pkg,
        "win32com.propsys.propsys": fake_propsys,
        "win32com.propsys.pscon": fake_pscon,
        "win32com.shell": fake_shell_pkg,
        "win32com.shell.shellcon": fake_shellcon,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(notify.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(notify.sys, "argv", [str(tmp_path / "run.py"), "--no-ui"])
    monkeypatch.setattr(notify, "_windows_registration_result", None)

    assert notify._register_windows_aumid() is True
    assert any(call[0] == "CreateShortcut" for call in shortcut_calls)
    assert ("Save",) in shortcut_calls
    assert property_calls[0][0] == "SetValue"
    assert property_calls[0][1] == "PKEY_AppUserModel_ID"
    assert property_calls[0][2] == ("PROPVARIANT", "SteamSwap")
    assert property_calls[-1] == ("Commit",)


def test_payment_wait_notifies_windows_without_pushplus(monkeypatch, capsys):
    from app import pipeline_steps

    pending = []
    notices = []
    monkeypatch.setattr(
        pipeline_steps,
        "send_windows_toast",
        lambda title, message: notices.append((title, message)) or True,
    )

    paid = pipeline_steps._do_payment_notify_and_wait(
        {"name": "Test Item"},
        {"notify": {}},
        12.5,
        2,
        "https://pay.example/order",
        "wechat",
        "order-1",
        10.0,
        pending.append,
        lambda **_kwargs: False,
        lambda _ok: None,
        lambda: False,
        None,
    )

    assert paid is False
    assert notices == [("SteamSwap: BUFF 待付款", "Test Item\n数量: 2，总价: 25.00 元\n付款方式: wechat")]
    assert "https://pay.example/order" in capsys.readouterr().out
    assert pending == [{"pay_url": "https://pay.example/order", "pay_type": "wechat", "name": "Test Item", "order_id": "order-1"}, None]


@pytest.mark.parametrize("confirmed", [True, False])
def test_browser_confirmation_updates_terminal(monkeypatch, capsys, confirmed):
    import threading
    from app import pipeline_steps, state
    from app.routes import status

    current = state.State()
    monkeypatch.setattr(state, "get_state", lambda: current)
    reader_finished = threading.Event()
    waiting = threading.Event()
    monkeypatch.setattr(
        pipeline_steps, "_start_console_payment_confirmation",
        lambda *_args: reader_finished,
    )
    results = []

    def wait(**kwargs):
        waiting.set()
        return current.wait_payment_confirm(**kwargs)

    def run():
        results.append(pipeline_steps._do_payment_notify_and_wait(
            {"name": "Test Item"},
            {"notify": {"windows_toast_enabled": False, "email_timeout_seconds": 2}},
            1.0, 1, "https://pay.example/order", "wechat", "order-1", 0.0,
            current.set_pending_payment, wait, current.confirm_payment,
            current.is_stop_requested, None,
        ))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert waiting.wait(2)
        status.api_confirm_payment(status.ConfirmBody(ok=confirmed))
        worker.join(2)
        assert not worker.is_alive()
    finally:
        current.request_stop()
        worker.join(2)

    assert results == [confirmed]
    assert reader_finished.is_set()
    assert current.get_pending_payment() is None
    output = capsys.readouterr().out
    assert ("已收到付款确认，继续处理订单" if confirmed else "已放弃本次采买，将跳过该饰品") in output


@pytest.mark.parametrize("outcome", ["timeout", "stop", "error"])
def test_terminal_reports_payment_wait_ending(monkeypatch, capsys, outcome):
    from app import pipeline_steps

    monkeypatch.setattr(pipeline_steps, "_start_console_payment_confirmation", lambda *_args: None)
    pending = []

    def wait(**_kwargs):
        if outcome == "error":
            raise RuntimeError("private error details")
        return None

    def run():
        return pipeline_steps._do_payment_notify_and_wait(
            {"name": "Test Item"}, {"notify": {"windows_toast_enabled": False}},
            1.0, 1, "https://pay.example/order", "wechat", "order-1", 0.0,
            pending.append, wait, lambda _ok: None, lambda: outcome == "stop", None,
        )

    if outcome == "error":
        with pytest.raises(RuntimeError, match="private error details"):
            run()
    else:
        assert run() is None
    expected = {
        "timeout": "付款等待已超时",
        "stop": "付款等待已停止",
        "error": "付款等待异常结束，请查看应用日志核对订单状态",
    }
    output = capsys.readouterr().out
    assert expected[outcome] in output
    assert "private error details" not in output
    assert pending[-1] is None


@pytest.mark.parametrize("email_result, expected", [("timeout", None), ("fail", False), ("success", True)])
def test_email_timeout_is_not_an_explicit_abandonment(monkeypatch, email_result, expected):
    from types import SimpleNamespace
    from app import pipeline_steps, state

    current = state.State()
    monkeypatch.setattr(pipeline_steps, "_start_console_payment_confirmation", lambda *_args: None)
    monkeypatch.setattr(pipeline_steps, "wait_email_command", lambda *_args, **_kwargs: email_result)
    monkeypatch.setattr(pipeline_steps.threading, "Thread", lambda target, **_kwargs: SimpleNamespace(start=target))
    result = pipeline_steps._do_payment_notify_and_wait(
        {"name": "Test Item"}, {"notify": {
            "windows_toast_enabled": False, "email_user": "test", "email_pass": "test",
            "email_timeout_seconds": 0,
        }},
        1.0, 1, "https://pay.invalid/test", "alipay", "order-1", 0.0,
        current.set_pending_payment, current.wait_payment_confirm, current.confirm_payment,
        current.is_stop_requested, None,
    )
    assert result is expected


def test_old_email_listener_cannot_confirm_the_next_order(monkeypatch):
    import threading
    from app import pipeline_steps, state

    current = state.State()
    started = threading.Event()
    release = threading.Event()
    threads = []
    original_thread = threading.Thread

    def start_thread(**kwargs):
        thread = original_thread(**kwargs)
        threads.append(thread)
        return thread

    def email_wait(*_args, **kwargs):
        started.set()
        assert release.wait(2)
        assert kwargs["is_stop_requested"]()
        return "success"

    def wait(**kwargs):
        assert started.wait(2)
        current.confirm_payment(False)
        return current.wait_payment_confirm(**kwargs)

    monkeypatch.setattr(pipeline_steps, "_start_console_payment_confirmation", lambda *_args: None)
    monkeypatch.setattr(pipeline_steps, "wait_email_command", email_wait)
    monkeypatch.setattr(pipeline_steps.threading, "Thread", start_thread)
    try:
        assert pipeline_steps._do_payment_notify_and_wait(
            {"name": "Test Item"}, {"notify": {
                "windows_toast_enabled": False, "email_user": "test", "email_pass": "test",
            }},
            1.0, 1, "https://pay.invalid/test", "alipay", "order-1", 0.0,
            current.set_pending_payment, wait, current.confirm_payment,
            current.is_stop_requested, None,
        ) is False
        current.set_pending_payment({"order_id": "order-2"})
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
    assert current.wait_payment_confirm(timeout_seconds=0) is None


def test_console_payment_command_accepts_paid_and_cancel_values():
    from app import pipeline_steps

    assert pipeline_steps._parse_console_payment_command("y") is True
    assert pipeline_steps._parse_console_payment_command("已完成支付") is True
    assert pipeline_steps._parse_console_payment_command("n") is False
    assert pipeline_steps._parse_console_payment_command("取消") is False
    assert pipeline_steps._parse_console_payment_command("later") is None


def test_console_payment_confirmation_uses_the_shared_confirmation_callback(monkeypatch):
    from app import pipeline_steps

    class InteractiveStdin:
        def isatty(self):
            return True

    confirmations = []
    monkeypatch.setattr(pipeline_steps.sys, "stdin", InteractiveStdin())
    monkeypatch.setattr(
        pipeline_steps,
        "_read_console_payment_command",
        lambda _finished: "paid",
    )

    finished = pipeline_steps._start_console_payment_confirmation(
        confirmations.append,
        lambda: False,
        None,
    )
    deadline = time.monotonic() + 1
    while not confirmations and time.monotonic() < deadline:
        time.sleep(0.01)
    if finished is not None:
        finished.set()

    assert confirmations == [True]
