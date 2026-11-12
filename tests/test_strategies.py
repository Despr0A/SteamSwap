from app import strategy_engine as se


def test_default_buy_strategy_contains_current_protection_chain():
    strategy = se.get_strategy("system.buy.default")
    enabled = {step["module_id"] for step in strategy["steps"] if step.get("enabled") is not False}

    assert {
        "buy.steamdt_top_n",
        "buy.exclude_keywords",
        "buy.buff_realtime_price",
        "buy.steam_sell_depth",
        "guard.sell_pressure",
        "guard.max_discount",
        "guard.history_data_window",
        "guard.volatility_cv",
        "guard.trend_quality",
        "guard.price_position",
        "guard.purchase_hard_cap",
        "guard.purchase_liquidity_cap",
        "guard.low_price_purchase_guard",
        "guard.held_same_item_guard",
        "guard.target_balance",
        "action.buff_lock_pay",
    } <= enabled


def test_activation_rejects_enabled_user_module(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    se.import_user_module({
        "id": "custom.buy.my_filter",
        "name": "我的过滤",
        "strategy_types": ["buy"],
    })
    strategy = {
        "id": "custom.buy.with_module",
        "name": "含用户模块",
        "strategy_type": "buy",
        "origin": "custom",
        "steps": [
            {"module_id": "custom.buy.my_filter", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }

    errors = se.validate_strategy(strategy, for_activation=True)

    assert any("用户模块" in e for e in errors)


def test_import_sanitizes_sensitive_fields_and_does_not_override_system_id():
    imported = se.normalize_strategy({
        "id": "system.buy.default",
        "name": "导入暂停",
        "strategy_type": "buy",
        "cookies": "secret",
        "steps": [
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {"token": "secret", "note": "ok"}},
        ],
    }, imported=True)

    assert imported["id"].startswith("custom.buy.")
    assert "cookies" not in imported
    assert imported["steps"][0]["params"] == {"note": "ok"}


def test_simulate_marks_trade_actions_as_dry_run():
    result = se.simulate_strategy({"strategy_id": "system.buy.default"})
    assert result["ok"] is True
    action = [r for r in result["results"] if r["module_id"] == "action.buff_lock_pay"][0]
    assert action["status"] == "action"
    assert "跳过真实交易" in action["reason"]


def test_validation_rejects_duplicate_conflict_dependency_and_step_limit():
    duplicate = {
        "name": "重复",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.steamdt_top_n", "enabled": False, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }
    assert any("最多只能添加" in e for e in se.validate_strategy(duplicate))

    dependency = {
        "name": "缺依赖",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "guard.max_discount", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }
    assert any("需要同时启用" in e and "Steam 卖单深度" in e for e in se.validate_strategy(dependency))

    conflict = {
        "name": "互斥",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "guard.history_stability", "enabled": True, "params": {}},
            {"module_id": "guard.volatility_cv", "enabled": True, "params": {}},
            {"module_id": "guard.price_position", "enabled": True, "params": {}},
        ],
    }
    assert any("互斥" in e for e in se.validate_strategy(conflict, for_activation=True))

    oversized = {
        "name": "过长",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": False, "params": {}}
            for _ in range(se.STRATEGY_STEP_LIMITS["buy"] + 1)
        ],
    }
    assert any("最多添加" in e for e in se.validate_strategy(oversized))


def test_declarative_user_module_can_activate_and_simulate(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    module = se.import_user_module({
        "id": "custom.buy.price_floor",
        "name": "Price floor",
        "module_kind": "declarative",
        "strategy_types": ["buy"],
        "uses_modules": ["buy.steam_sell_depth"],
        "stage": "buy.candidate_guard",
        "conditions": [
            {"left": "outputs.buy.steam_sell_depth.smart_price", "op": "gte", "value": 20}
        ],
        "fail_status": "reject",
        "message": "Reference price is below the custom floor",
    })
    assert module["enabled"] is True

    strategy = {
        "name": "Declarative buy guard",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "custom.buy.price_floor", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }

    assert se.validate_strategy(strategy, for_activation=True) == []
    result = se.simulate_strategy({"strategy": strategy})
    custom = [r for r in result["results"] if r["module_id"] == "custom.buy.price_floor"][0]
    assert custom["status"] == "reject"
    assert custom["output"]["total_conditions"] == 1


def test_declarative_runtime_guard_blocks_at_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    monkeypatch.setattr(se, "STRATEGY_STORE_PATH", tmp_path / "strategies.json")
    se.import_user_module({
        "id": "custom.buy.min_volume",
        "name": "Minimum daily volume",
        "module_kind": "declarative",
        "strategy_types": ["buy"],
        "stage": "buy.candidate_guard",
        "conditions": [{"left": "item.daily_volume", "op": "gte", "value": 100}],
        "fail_status": "reject",
    })
    saved = se.save_strategy({
        "name": "Buy with volume guard",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "custom.buy.min_volume", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    })
    cfg = se.apply_strategy_to_config({
        "strategies": {"active_buy_strategy_id": saved["id"]},
        "pipeline": {},
        "stability": {},
    }, "buy")

    results, blocking = se.evaluate_strategy_runtime_modules(
        cfg,
        "buy",
        "buy.candidate_guard",
        context={"item": {"daily_volume": 10}},
        outputs={},
    )

    assert blocking["module_id"] == "custom.buy.min_volume"
    assert blocking["status"] == "reject"
    assert len(results) == 1


def test_declarative_user_module_dependency_order_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    se.import_user_module({
        "id": "custom.buy.needs_price",
        "name": "Needs price",
        "module_kind": "declarative",
        "strategy_types": ["buy"],
        "uses_modules": ["buy.steam_sell_depth"],
        "stage": "buy.candidate_guard",
        "conditions": [{"left": "outputs.buy.steam_sell_depth.smart_price", "op": "exists"}],
    })
    strategy = {
        "name": "Bad order",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "custom.buy.needs_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }

    errors = se.validate_strategy(strategy, for_activation=True)

    assert any("must run after" in e for e in errors)


def test_external_code_user_module_still_cannot_activate(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    se.import_user_module({
        "id": "custom.buy.code_filter",
        "name": "Code filter",
        "strategy_types": ["buy"],
        "entrypoint": "module:run",
    })
    strategy = {
        "name": "Code module strategy",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "custom.buy.code_filter", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }

    errors = se.validate_strategy(strategy, for_activation=True)

    assert any("not a safe declarative module" in e for e in errors)


def test_step_param_validation_rejects_out_of_range_value():
    strategy = {
        "name": "Bad params",
        "strategy_type": "buy",
        "steps": [
            {"module_id": "buy.steamdt_top_n", "enabled": True, "params": {}},
            {"module_id": "buy.basic_candidate_filter", "enabled": True, "params": {}},
            {"module_id": "buy.buff_realtime_price", "enabled": True, "params": {}},
            {"module_id": "buy.steam_sell_depth", "enabled": True, "params": {}},
            {"module_id": "guard.target_balance", "enabled": True, "params": {"target_balance": -1}},
            {"module_id": "action.buff_lock_pay", "enabled": True, "params": {}},
        ],
    }

    assert any(">= 0" in e for e in se.validate_strategy(strategy))


def test_system_strategy_params_can_be_saved_to_config(monkeypatch):
    state = {
        "strategies": {"active_buy_strategy_id": "system.buy.default"},
        "pipeline": {"target_balance": 100},
        "stability": {},
    }

    def fake_load():
        return se.copy.deepcopy(state)

    def fake_save(data):
        state.clear()
        state.update(se.copy.deepcopy(data))

    monkeypatch.setattr(se, "load_app_config_validated", fake_load)
    monkeypatch.setattr(se, "save_app_config_validated", fake_save)

    strategy = se.get_strategy("system.buy.default")
    for step in strategy["steps"]:
        if step["module_id"] == "guard.target_balance":
            step["params"] = {"target_balance": 233}

    saved = se.save_strategy(strategy)

    assert saved["id"] == "system.buy.default"
    assert saved["origin"] == "system"
    assert state["pipeline"]["target_balance"] == 233


def test_system_strategy_params_can_restore_schema_defaults(monkeypatch):
    state = {
        "strategies": {"active_buy_strategy_id": "system.buy.default"},
        "pipeline": {"target_balance": 233},
        "stability": {},
    }

    def fake_load():
        return se.copy.deepcopy(state)

    def fake_save(data):
        state.clear()
        state.update(se.copy.deepcopy(data))

    monkeypatch.setattr(se, "load_app_config_validated", fake_load)
    monkeypatch.setattr(se, "save_app_config_validated", fake_save)

    strategy = se.get_strategy("system.buy.default")
    modules = se._module_by_id()
    for step in strategy["steps"]:
        schema = (modules.get(step["module_id"]) or {}).get("params_schema") or {}
        step["params"] = {
            key: se.copy.deepcopy(meta["default"])
            for key, meta in schema.items()
            if isinstance(meta, dict) and "default" in meta
        }

    saved = se.save_strategy(strategy)

    assert saved["id"] == "system.buy.default"
    assert state["pipeline"]["target_balance"] == 100


def test_system_strategy_save_rejects_structure_changes():
    strategy = se.get_strategy("system.buy.default")
    strategy["steps"] = strategy["steps"][1:]

    try:
        se.save_strategy(strategy)
    except se.StrategyError as exc:
        assert "只允许修改参数" in str(exc)
    else:
        raise AssertionError("system strategy structure changes should be rejected")
