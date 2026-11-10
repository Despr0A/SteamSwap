"""Offline Edge UI checks. Run directly with the existing Playwright environment.

All requests are intercepted: no application server or trading API is contacted.
Screenshots are written to the directory passed as the first command argument.
"""

import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".pytest-ui-artifacts"
TABS = ["auto", "purchases", "accounts", "steam-guard", "proxy", "strategies", "settings", "debug"]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors = []
    requests = []
    fail_paths = set()
    fixtures = {
        "/api/runtime": {"runtime": {"can_launch_headful_browser": False}},
        "/api/config": {"config": {"system": {"ui_scale": "1.0"}}},
        "/api/accounts": {"accounts": [], "current_id": ""},
        "/api/status": {"status": "idle", "step": "等待启动任务"},
        "/api/pending_payment": {"pending": None},
        "/api/purchases": {"purchases": []},
        "/api/proxy/config": {"proxy_pool": {"strategy": 1, "proxies": []}},
        "/api/strategies": {"strategies": [], "modules": [], "active": {}},
        "/api/log": {"lines": [], "next_index": 0},
        "/api/steam_guard": {"ok": True, "code": "ABCDE", "period": 30},
    }

    def route_request(route):
        path = urlparse(route.request.url).path
        if path.startswith("/api/"):
            requests.append((route.request.method, path))
            if path in fail_paths:
                route.fulfill(status=503, json={"detail": "模拟服务暂时不可用"})
                return
            route.fulfill(json=fixtures.get(path, {"ok": True}))
            return
        if urlparse(route.request.url).netloc != "steamswap.test":
            route.fulfill(status=200, body="", content_type="application/javascript")
            return
        asset = ROOT / "web" / (path.lstrip("/") or "index.html")
        route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "text/plain")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        context.route("**/*", route_request)
        context.add_init_script("localStorage.setItem('steamswap_onboard_skip', '1')")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://steamswap.test/")
        page.wait_for_function("document.querySelector('#status-text').textContent === '空闲中'")
        assert page.locator("html").get_attribute("data-theme") == "light"

        for width in [1440, 1024, 768, 420, 360]:
            page.set_viewport_size({"width": width, "height": 1080 if width > 768 else 900})
            for tab in TABS:
                page.locator(f'.nav-item[data-tab="{tab}"]').click()
                page.wait_for_function("name => document.querySelector('#panel-' + name).classList.contains('active')", arg=tab)
                assert page.locator(f'.nav-item[data-tab="{tab}"]').get_attribute("aria-current") == "page"
                sizes = page.evaluate("""() => {
                    const panel = document.querySelector('.panel.active');
                    const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
                    return {body: document.body.scrollWidth * Number(getComputedStyle(document.documentElement).zoom), width: innerWidth,
                        panel: panel.scrollWidth, client: panel.clientWidth,
                        sidebarLeft: sidebar.left, sidebarRight: sidebar.right,
                        contentLeft: document.querySelector('.main-content').getBoundingClientRect().left};
                }""")
                if sizes["body"] > sizes["width"] or sizes["panel"] > sizes["client"] + 1:
                    page.screenshot(path=str(OUTPUT / "layout-failure.png"))
                    print(page.evaluate("""() => [...document.styleSheets].map(s => ({href:s.href, rules:s.cssRules.length}))"""))
                    print(page.evaluate("""() => ['.app-container','.sidebar','.main-content','.panel.active'].map(s => {const n=document.querySelector(s), c=getComputedStyle(n);return {s,width:c.width,flex:c.flex,minWidth:c.minWidth,padding:c.padding,display:c.display}})"""))
                assert sizes["body"] <= sizes["width"], (width, tab, sizes)
                assert sizes["panel"] <= sizes["client"] + 1, (width, tab, sizes)
                assert sizes["sidebarLeft"] == 0 and sizes["sidebarRight"] <= sizes["contentLeft"] + 1, sizes
                if width in (1440, 420):
                    page.screenshot(path=str(OUTPUT / f"{tab}-{width}.png"))

        page.set_viewport_size({"width": 1440, "height": 1080})
        for target in ["accounts", "strategies", "debug", "settings"]:
            page.locator('.nav-item[data-tab="auto"]').click()
            page.locator(f'[data-open-panel="{target}"]').click()
            assert page.locator(f"#panel-{target}").is_visible()
            assert page.locator("#page-title-display").evaluate("node => node === document.activeElement")

        page.locator('.nav-item[data-tab="accounts"]').click()
        page.locator("#btn-add-account").click()
        assert page.locator("#account-form-overlay").is_visible()
        page.screenshot(path=str(OUTPUT / "account-dialog.png"))
        page.locator("#account-form-cancel").click()
        assert not page.locator("#account-form-overlay").is_visible()
        page.locator("#accounts-search").fill("missing account")
        assert page.locator(".account-item").count() == 0
        page.locator("#accounts-search").fill("")

        fixtures["/api/accounts"] = {"current_id": "demo", "accounts": [{
            "id": "demo", "username": "demo_account", "display_name": "界面测试账号（模拟数据）",
            "steam_id": "76561198000000000", "region_code": "CN", "currency_code": "CNY",
        }]}
        fixtures["/api/purchases"] = {"purchases": [{"name": "模拟饰品 · 用于检查长名称换行和表格滚动", "price": 12.34, "market_price": 16.78, "purchase_ratio": 0.7354, "at": 1794240000}]}
        fixtures["/api/strategies"] = {
            "strategies": [{"id": "demo", "name": "界面测试策略", "strategy_type": "buy", "origin": "system",
                "description": "仅用于离线布局检查的模拟策略。", "steps": [{"module_id": "buy.steamdt_top_n", "enabled": True, "params": {"iflow_top_n": 50}}]}],
            "modules": [{"id": "buy.steamdt_top_n", "name": "SteamDT 取前 N 条", "strategy_types": ["buy"],
                "description": "限制本轮候选检查数量。", "params_schema": {"iflow_top_n": {"type": "integer", "default": 50, "label": "候选数量"}}}],
            "active": {"buy": "demo"},
        }
        page.evaluate("loadStrategies(true)")
        page.evaluate("refreshTransactions()")
        page.evaluate("refreshAccounts()")
        for width in [1440, 420, 360]:
            page.set_viewport_size({"width": width, "height": 1000})
            for tab in ["accounts", "purchases", "strategies"]:
                page.locator(f'.nav-item[data-tab="{tab}"]').click()
                if tab == "accounts":
                    page.locator('.account-item[data-id="demo"]').click()
                assert page.locator(f"#panel-{tab}").evaluate("node => node.scrollWidth <= node.clientWidth + 1"), ("populated", width, tab)
                page.screenshot(path=str(OUTPUT / f"{tab}-populated-{width}.png"))

        page.set_viewport_size({"width": 1440, "height": 1080})
        page.locator('.nav-item[data-tab="settings"]').click()
        for scale in ["0.7", "1.2", "1.0"]:
            page.locator("#cfg-ui_scale").select_option(scale)
            assert page.locator("html").evaluate("node => node.style.zoom") == str(float(scale)).rstrip("0").rstrip(".")
            assert page.locator("#panel-settings").evaluate("node => node.scrollWidth <= node.clientWidth + 1")

        page.locator('.nav-item[data-tab="proxy"]').click()
        page.locator('.proxy-strategy-card[data-strategy="2"]').focus()
        page.keyboard.press("Space")
        assert page.locator('.proxy-strategy-card[data-strategy="2"]').get_attribute("aria-pressed") == "true"
        assert page.locator('.proxy-strategy-card[data-strategy="2"]').evaluate("node => getComputedStyle(node).outlineWidth") == "2px"

        page.locator('.nav-item[data-tab="steam-guard"]').click()
        page.locator("#steam-token-circle").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('#steam-code-text').textContent === 'ABCDE'")

        page.locator('.nav-item[data-tab="debug"]').click()
        page.evaluate("""() => {
            logLines = [
                {id: 1, msg: '模拟筛选报告', report: {layer: 'item', report_id: 'ui-check', item: '模拟饰品', decision: 'reject', checks: [{check: 'max_discount', status: 'reject', limit: 0.7}]}},
                {id: 2, msg: '模拟网络报告', report: {layer: 'network', report_id: 'ui-check', status: 'success', source: 'database_cache'}}
            ]; renderLogFull();
        }""")
        page.locator(".log-report summary").click()
        page.set_viewport_size({"width": 420, "height": 900})
        page.screenshot(path=str(OUTPUT / "report-expanded-mobile.png"))
        assert page.locator("#panel-debug").evaluate("node => node.scrollWidth <= node.clientWidth + 1")
        page.locator("[data-report-network]").click()
        assert page.locator("#log-layer").input_value() == "network"
        assert "本地数据库" in page.locator("#log-output").text_content()

        fail_paths.add("/api/purchases")
        page.evaluate("refreshTransactions()")
        assert page.locator("#toast-host").get_by_text("加载已支付饰品失败").is_visible()
        fail_paths.clear()

        page.locator('.nav-item[data-tab="auto"]').click()
        fixtures["/api/status"] = {"status": "running", "step": "模拟运行进度"}
        fixtures["/api/pending_payment"] = {"pending": {"pay_url": "https://example.test/payment", "name": "模拟待支付订单", "pay_type": "alipay"}}
        page.evaluate("refreshStatus()")
        assert page.locator("#pending-payment").is_visible()
        assert page.locator("#btn-paid").is_visible()
        page.screenshot(path=str(OUTPUT / "payment-desktop.png"))
        page.set_viewport_size({"width": 420, "height": 900})
        assert page.locator("#panel-auto").evaluate("node => node.scrollWidth <= node.clientWidth")
        page.locator("#btn-paid").scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        page.screenshot(path=str(OUTPUT / "payment-mobile.png"))

        page.set_viewport_size({"width": 1440, "height": 1080})
        page.locator("#btn-theme").click()
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.screenshot(path=str(OUTPUT / "dark-desktop.png"))
        page.evaluate("Theme.set('light')")
        page.evaluate("_showWizard()")
        assert page.locator("#onboard-wizard-overlay").is_visible()
        page.screenshot(path=str(OUTPUT / "onboarding.png"))
        assert not errors, errors
        assert not [request for request in requests if request[0] != "GET"], requests
        browser.close()
    print(json.dumps({"result": "passed", "layouts": 49, "page_errors": errors, "screenshots": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
