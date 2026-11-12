def test_first_payment_confirmation_wins_until_the_next_pending_order():
    from app.state import State

    state = State()
    state.set_pending_payment({"order_id": "one"})
    state.confirm_payment(True)
    state.confirm_payment(False)

    assert state.wait_payment_confirm(timeout_seconds=0) is True

    state.set_pending_payment({"order_id": "two"})
    state.confirm_payment(False)

    assert state.wait_payment_confirm(timeout_seconds=0) is False


def test_timeout_and_stop_are_distinct_from_explicit_abandonment():
    from app.state import State

    state = State()
    state.set_pending_payment({"order_id": "timeout"})
    assert state.wait_payment_confirm(timeout_seconds=0) is None
    state.set_pending_payment({"order_id": "stopped"})
    state.request_stop()
    assert state.wait_payment_confirm() is None
