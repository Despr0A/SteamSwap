def test_reference_price_first_observation_is_rejected(tmp_path, monkeypatch):
    from app import reference_price

    monkeypatch.setattr(reference_price, "_DB_PATH", tmp_path / "reference.db")

    result = reference_price.observe_reference_price(
        "AK-47 | Redline", 100, now=1_000
    )

    assert result["first_seen"] is True
    assert result["stable"] is False
    assert result["stable_observations"] == 1


def test_reference_price_nearby_observation_is_allowed(tmp_path, monkeypatch):
    from app import reference_price

    monkeypatch.setattr(reference_price, "_DB_PATH", tmp_path / "reference.db")

    reference_price.observe_reference_price("AK-47 | Redline", 100, now=1_000)
    result = reference_price.observe_reference_price("AK-47 | Redline", 110, now=2_000)

    assert result["stable"] is True
    assert result["stable_observations"] == 2
    assert result["change_ratio"] == 0.1


def test_reference_price_jump_resets_stability_count(tmp_path, monkeypatch):
    from app import reference_price

    monkeypatch.setattr(reference_price, "_DB_PATH", tmp_path / "reference.db")

    reference_price.observe_reference_price("AK-47 | Redline", 100, now=1_000)
    reference_price.observe_reference_price("AK-47 | Redline", 110, now=2_000)
    result = reference_price.observe_reference_price("AK-47 | Redline", 150, now=3_000)

    assert result["stable"] is False
    assert result["stable_observations"] == 1
    assert result["reason"] == "参考价变动过大，重新观望"


def test_reference_price_app_ids_are_isolated(tmp_path, monkeypatch):
    from app import reference_price

    monkeypatch.setattr(reference_price, "_DB_PATH", tmp_path / "reference.db")

    reference_price.observe_reference_price("Same Name", 100, app_id=730, now=1_000)
    result = reference_price.observe_reference_price("Same Name", 100, app_id=570, now=2_000)

    assert result["first_seen"] is True
    assert result["stable"] is False
