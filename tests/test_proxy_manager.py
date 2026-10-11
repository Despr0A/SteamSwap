import sys
import threading
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _构造代理管理器(entries: list):
    """用假数据直接构造 ProxyManager，跳过读配置文件"""
    from utils.proxy_manager import ProxyManager
    with patch.object(ProxyManager, "_reload", return_value=None):
        mgr = ProxyManager.__new__(ProxyManager)
        mgr._lock = threading.Lock()
        mgr._warming_up = False
        mgr._proxies = entries
        mgr._cached_strategy = 2  # 完全走代理
        mgr._cached_enabled = True
        mgr._proxy_configs = [p["config"] for p in entries]
        mgr._proxy_weights = [max(0, p["score"]) for p in entries]
        mgr._last_disabled_proxy_log_key = None
        mgr._last_disabled_proxy_log_at = 0.0
    return mgr


def test_代理_score为0的永远不被选():
    entries = [
        {"config": {"host": "好的", "port": 80},  "score": 1000},
        {"config": {"host": "坏的",  "port": 80},  "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    结果 = [mgr.get_next_proxy_dict() for _ in range(200)]
    hosts = [d["http"].split("//")[1].split(":")[0] for d in 结果 if d]
    assert "坏的" not in hosts, f"score=0的代理不应被选中: {Counter(hosts)}"
    assert "好的" in hosts


def test_代理_空池返回None():
    mgr = _构造代理管理器([])
    assert mgr.get_next_proxy_dict() is None


def test_代理_全挂了返回None():
    # 所有代理score都是0
    entries = [
        {"config": {"host": "死的1", "port": 80}, "score": 0},
        {"config": {"host": "死的2", "port": 80}, "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    assert mgr.get_next_proxy_dict() is None


def test_代理_高分代理被选得明显更多():
    # score=9000 vs score=100，跑500次看分布
    entries = [
        {"config": {"host": "快代理", "port": 80}, "score": 9000},
        {"config": {"host": "慢代理", "port": 80}, "score": 100},
    ]
    mgr = _构造代理管理器(entries)
    结果 = [mgr.get_next_proxy_dict() for _ in range(500)]
    hosts = [d["http"].split("//")[1].split(":")[0] for d in 结果 if d]
    counts = Counter(hosts)
    # 快代理至少应该比慢代理多5倍
    assert counts["快代理"] > counts["慢代理"] * 5, f"高分代理没被优先选: {counts}"


def test_代理池关闭时不预热():
    entries = [
        {"config": {"host": "不会测速", "port": 80}, "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    mgr._cached_enabled = False
    mgr._cached_strategy = 3
    with patch("utils.proxy_manager.test_one_proxy") as mock_test:
        mgr.warmup()
    mock_test.assert_not_called()
    assert mgr._warming_up is False


def test_新配置代理在异步预热完成前可用():
    from utils import proxy_manager

    config = {
        "enabled": True,
        "strategy": 2,
        "proxies": [{"host": "fresh-proxy.example", "port": 8080}],
    }
    with patch.object(proxy_manager, "_load_proxy_pool_cfg", return_value=config):
        mgr = proxy_manager.ProxyManager()

    selected = mgr.get_proxies_for_request()

    assert selected == {
        "http": "http://fresh-proxy.example:8080/",
        "https": "http://fresh-proxy.example:8080/",
    }


def test_测速成功但Steam拒绝的代理不能通过预热(monkeypatch):
    from utils import proxy_manager

    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return type("Response", (), {"status_code": 200 if len(calls) == 1 else 403, "text": "1.2.3.4"})()

    monkeypatch.setattr(proxy_manager.requests, "get", get)
    result = proxy_manager.test_one_proxy({"host": "proxy", "port": 80}, "https://ip.example/", 10, check_steam=True)
    assert result["status"] == "failed"
    assert "403" in result["error"]
    assert calls[1] == "https://steamcommunity.com/market/orderbook"


def test_预热必须检查Steam(monkeypatch):
    from utils import proxy_manager

    mgr = _构造代理管理器([{"config": {"host": "proxy", "port": 80}, "score": 1}])
    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {})
    with patch.object(proxy_manager, "test_one_proxy", return_value={"status": "failed"}) as probe:
        mgr.warmup()
    assert probe.call_args.kwargs["check_steam"] is True
    assert mgr.get_next_proxy_dict() is None


def test_retry_excludes_used_and_failed_proxy_endpoints():
    mgr = _构造代理管理器([
        {"config": {"host": "used", "port": 80}, "score": 1000},
        {"config": {"host": "fresh", "port": 80}, "score": 1},
        {"config": {"host": "fresh", "port": 80}, "score": 1},
        {"config": {"host": "failed", "port": 80}, "score": 0},
    ])
    excluded = {"http://used:80/"}
    selected = mgr.get_next_proxy_dict(excluded=excluded)
    assert selected["http"] == "http://fresh:80/"
    excluded.add(selected["http"])
    assert mgr.get_next_proxy_dict(excluded=excluded) is None


def test_Steam返回订单簿才允许代理通过(monkeypatch):
    from utils import proxy_manager

    payloads = [
        {"data": {"success": True, "data": {"rgCompactSellOrders": [100, 1]}}},
        {"success": False, "data": {"rgCompactSellOrders": [100, 1]}},
        {"data": {"message": "login required"}},
    ]
    for payload, expected in zip(payloads, ["ok", "failed", "failed"]):
        response = type("Response", (), {
            "status_code": 200, "text": "1.2.3.4", "json": lambda self: payload
        })()
        monkeypatch.setattr(proxy_manager.requests, "get", lambda *a, **k: response)
        result = proxy_manager.test_one_proxy({"host": "proxy", "port": 80}, "https://ip.example/", 10, check_steam=True)
        assert result["status"] == expected


def test_failed_proxy_is_excluded_before_other_probes_finish(monkeypatch):
    from utils import proxy_manager

    mgr = _构造代理管理器([
        {"config": {"host": "bad", "port": 80}, "score": 1},
        {"config": {"host": "pending", "port": 80}, "score": 1},
    ])
    monkeypatch.setattr(proxy_manager, "_load_proxy_pool_cfg", lambda: {})
    monkeypatch.setattr(proxy_manager, "test_one_proxy", lambda *args, **kwargs: {"status": "failed", "error": "407 Proxy Authentication Required"})
    monkeypatch.setattr(proxy_manager.random, "choices", lambda configs, **kwargs: [configs[0]])

    def completed(futures):
        ordered = list(futures)
        yield ordered[0]
        assert mgr.get_next_proxy_dict()["http"] == "http://pending:80/"
        yield ordered[1]

    monkeypatch.setattr(proxy_manager, "as_completed", completed)
    mgr.warmup()
    assert mgr.get_next_proxy_dict() is None


def test_connect_407_is_reported_as_proxy_auth_required(monkeypatch):
    from utils import proxy_manager
    import requests

    def fail(*args, **kwargs):
        raise requests.exceptions.ProxyError("Unable to connect to proxy") from OSError("Tunnel connection failed: 407 Proxy Authentication Required")

    monkeypatch.setattr(proxy_manager.requests, "get", fail)
    result = proxy_manager.test_one_proxy({"host": "bad", "port": 80}, "https://ip.example/", 10)
    assert "code=PROXY_AUTH_REQUIRED" in result["error"]
