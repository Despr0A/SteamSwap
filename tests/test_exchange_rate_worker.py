def test_fetch_exchange_rates_inverts_cny_base_snapshot(monkeypatch):
    from app.services import workers

    class Response:
        def json(self):
            return {"result": "success", "rates": {"USD": 0.5, "JPY": 100}}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr("utils.proxy_manager.get_proxy_manager", lambda: type("PM", (), {
        "get_proxies_for_request": lambda self: None,
    })())

    assert workers._fetch_exchange_rates("CNY") == {
        "CNY": 1.0,
        "USD": 2.0,
        "JPY": 0.01,
    }


def test_history_currency_conversion_does_not_use_static_usd_fallback(monkeypatch):
    from utils.money import apply_currency

    converted, currency = apply_currency([1.0], "USD", rate_map={})

    assert converted == [1.0]
    assert currency == "USD"
