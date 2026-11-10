"""Static checks for the web UI's HTML/JavaScript/API contracts."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _frontend_sources() -> tuple[str, str]:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "web" / "js").glob("*.js")
    )
    return html, js


def _backend_api_routes() -> set[str]:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "app").rglob("*.py")
    )
    return set(
        re.findall(
            r'@(?:router|app)\.(?:get|post|put|delete|patch)\(\s*["\'](/api/[^"\']+)',
            source,
        )
    )


def _frontend_api_paths(js: str) -> set[str]:
    paths = re.findall(r"API\s*\+\s*([\"'`])([^\"'`]+)\1", js)
    return {
        path.split("?", 1)[0].split("${", 1)[0]
        for _, path in paths
    }


def test_html_interactive_controls_are_referenced_by_javascript():
    html, js = _frontend_sources()
    ids = set(
        re.findall(
            r'<(?:button|input|select|textarea)\b[^>]*\bid="([^"]+)"',
            html,
        )
    )

    assert not [control_id for control_id in ids if control_id not in js]


def test_frontend_api_paths_exist_in_backend_routes():
    _, js = _frontend_sources()
    routes = _backend_api_routes()
    missing = []
    for path in sorted(_frontend_api_paths(js)):
        route_prefix = "/api" + path.rstrip("/")
        if not any(
            route == route_prefix
            or route.startswith(route_prefix + "/")
            for route in routes
        ):
            missing.append(path)

    assert not missing, f"Frontend API paths missing from backend routes: {missing}"


def test_account_controls_have_click_and_search_bindings():
    _, js = _frontend_sources()

    assert 'el("btn-add-account")?.addEventListener("click"' in js
    assert 'el("account-form-cancel")?.addEventListener("click"' in js
    assert 'el("account-form-save")?.addEventListener("click"' in js
    assert 'el("accounts-search")?.addEventListener("input"' in js
