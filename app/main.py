from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app.runtime_env import (
    env_bool,
    get_bind_host,
    get_external_url_hint,
    get_local_url,
    get_port,
    get_runtime_profile,
)


if sys.platform == "win32":
    os.chdir(Path(__file__).resolve().parent.parent)


def check_runtime_dependencies() -> None:
    try:
        import typing_extensions as te
    except Exception as exc:
        print(f"依赖检查失败: 无法导入 typing_extensions: {exc}")
        print("请运行: python -m pip install -r requirements.txt")
        sys.exit(1)
    missing = [name for name in ("Sentinel", "TypeAliasType") if not hasattr(te, name)]
    if missing:
        print("依赖版本过旧: typing_extensions 缺少 " + ", ".join(missing))
        print("FastAPI/Pydantic/SQLAlchemy 需要新版 typing_extensions。")
        print("请在项目目录运行: python -m pip install --upgrade typing_extensions")
        print("或运行: python -m pip install -r requirements.txt")
        sys.exit(1)


def _ensure_disclaimer() -> None:
    disclaimer_file = Path(".agreed_disclaimer")
    if disclaimer_file.exists():
        return
    if env_bool("STEAMSWAP_AGREE_DISCLAIMER", False):
        try:
            disclaimer_file.touch()
        except Exception:
            pass
        return
    if not sys.stdin.isatty():
        print("首次运行需要确认 README 中的免责声明。")
        print("交互式运行 python run.py 后输入 y，或确认已阅读后设置 STEAMSWAP_AGREE_DISCLAIMER=1。")
        sys.exit(1)
    print("本程序仅供学习，运行即代表同意 README 中的免责声明。")
    confirm = input("是否继续？(y/n): ")
    if confirm.strip().lower() != "y":
        sys.exit(0)
    try:
        disclaimer_file.touch()
    except Exception:
        pass


def _load_webview():
    try:
        import webview
    except Exception as exc:
        return None, exc
    return webview, None


def __getattr__(name: str):
    if name == "app":
        from app.api import app as api_app
        return api_app
    raise AttributeError(name)


def run_server(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    profile = get_runtime_profile()
    uvicorn.run(
        "app.api:app",
        host=host or get_bind_host(profile),
        port=port or get_port(),
        log_level="warning",
    )


def _parse_cli_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="SteamSwap service runner")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="start without opening WebView or a browser",
    )
    parser.add_argument(
        "--start-pipeline",
        action="store_true",
        help="start the buy pipeline after the service initializes",
    )
    parser.add_argument(
        "--observe-reference-prices",
        action="store_true",
        help="only observe Steam reference prices; requires --start-pipeline and never creates BUFF orders",
    )
    parser.add_argument("--host", help="override STEAMSWAP_HOST")
    parser.add_argument("--port", type=int, help="override STEAMSWAP_PORT")
    args = parser.parse_args(argv)
    if args.observe_reference_prices and not args.start_pipeline:
        parser.error("--observe-reference-prices requires --start-pipeline")
    return args


def _apply_cli_overrides(args) -> None:
    if args.no_ui:
        os.environ["STEAMSWAP_OPEN_BROWSER"] = "0"
    if args.host:
        os.environ["STEAMSWAP_HOST"] = args.host
    if args.port is not None:
        os.environ["STEAMSWAP_PORT"] = str(args.port)


def _start_pipeline_from_cli(*, observation_only: bool = False) -> bool:
    from app.config_loader import load_app_config_validated
    from app.database import init_db
    from app.pipeline import get_pipeline_start_blocker, start_pipeline

    init_db()
    blocker = get_pipeline_start_blocker()
    if blocker:
        print(f"无法从命令行启动流水线: {blocker.get('message') or blocker.get('code')}")
        return False
    config = load_app_config_validated()
    if observation_only:
        config = dict(config)
        config["pipeline"] = {
            **(config.get("pipeline") or {}),
            "reference_price_observation_only": True,
        }
    if not start_pipeline(config):
        print("无法从命令行启动流水线: 流水线已运行或当前凭证/对账状态不允许启动")
        return False
    if observation_only:
        print("参考价观测模式已从命令行启动：仅检测并记录价格，不会创建 BUFF 订单。")
    else:
        print("买入流水线已从命令行启动。待付款订单会显示 Windows 通知，并将付款链接输出到此终端。")
    return True


def _wait_for_server_thread(thread: threading.Thread) -> None:
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        pass


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _wait_for_server_ready(thread: threading.Thread, host: str, port: int, timeout: float = 20.0) -> bool:
    probe_host = _probe_host(host)
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        try:
            with socket.create_connection((probe_host, port), timeout=0.35):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main(argv: list[str] | None = None) -> None:
    args = _parse_cli_args(argv)
    _apply_cli_overrides(args)
    check_runtime_dependencies()
    _ensure_disclaimer()

    profile = get_runtime_profile()
    host = get_bind_host(profile)
    port = get_port()
    local_url = get_local_url(host, port)
    external_hint = get_external_url_hint(host, port)

    print(f"SteamSwap runtime: {profile.mode} ({profile.reason})")
    print(f"Listening on {external_hint}")

    if args.start_pipeline and not _start_pipeline_from_cli(
        observation_only=args.observe_reference_prices
    ):
        return

    if profile.mode == "server":
        print("未检测到可用桌面环境，已进入服务器模式。请使用外部浏览器访问上面的地址。")
        run_server(host, port)
        return
    if not profile.open_browser:
        print(f"已按配置禁用自动打开浏览器，请手动访问 {local_url}。")
        run_server(host, port)
        return

    thread = threading.Thread(target=run_server, args=(host, port), daemon=True)
    thread.start()
    if not _wait_for_server_ready(thread, host, port):
        if thread.is_alive():
            print(f"后端启动时间过长，暂不自动打开浏览器。服务就绪后请手动访问 {local_url}。")
            _wait_for_server_thread(thread)
        else:
            print("后端启动失败，请查看上方错误日志。")
        return

    webview, webview_error = _load_webview()
    if webview:
        try:
            webview.create_window("SteamSwap", local_url, width=1280, height=800, zoomable=True, maximized=True)
            webview.start()
            print(f"窗口已关闭，后端仍在运行。浏览器打开 {local_url} 可继续查看状态。按 Ctrl+C 退出。")
            _wait_for_server_thread(thread)
            return
        except Exception as exc:
            print(f"桌面窗口启动失败，改用系统浏览器: {exc}")
    elif webview_error:
        print(f"未启用内嵌桌面窗口，改用系统浏览器: {webview_error}")

    try:
        webbrowser.open(local_url)
    except Exception as exc:
        print(f"系统浏览器打开失败，请手动访问 {local_url}: {exc}")
    _wait_for_server_thread(thread)


if __name__ == "__main__":
    main()
