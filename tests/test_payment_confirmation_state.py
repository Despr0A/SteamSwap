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
