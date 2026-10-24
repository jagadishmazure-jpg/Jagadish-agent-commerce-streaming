from dataclasses import replace

import pytest
from conftest import KEY, NOW, cart, pay, submit

from agentcommerce.mandate import approve_cart, new_mandate
from agentcommerce.payments import FraudPolicy, PaymentError, PaymentService
from agentcommerce.pii import SafeError


def code(fn):
    with pytest.raises(PaymentError) as e:
        fn()
    return e.value.code


def test_happy_path(svc, mandate, token):
    r = pay(svc, mandate, token)
    assert r.amount == 2900 and r.status == "captured" and svc.captured_total() == 2900
    assert svc.last4(token) == "4242"


@pytest.mark.parametrize("n", ["4242 4242 4242 4241", "1234", "abcd efgh ijkl mnop", "", "4" * 25])
def test_bad_cards(svc, n):
    assert code(lambda: svc.tokenize(n)) == "card_invalid"


def test_bad_card_error_does_not_echo_number(svc):
    with pytest.raises(PaymentError) as e:
        svc.tokenize("4242 4242 4242 4241")  # card-shaped but fails Luhn, so scrub() would not mask it
    assert "4242" not in str(e.value)


def test_unknown_token(svc, mandate):
    assert code(lambda: pay(svc, mandate, "tok_nope")) == "token_unknown"
    assert code(lambda: svc.last4("tok_nope")) == "token_unknown"


def test_idempotent_retry_returns_same_receipt(svc, mandate, token):
    c = cart()
    ap = approve_cart(KEY, mandate, c, NOW)
    r1 = submit(svc, mandate, c, ap, token, "same")
    r2 = submit(svc, mandate, c, ap, token, "same")
    assert r1 is r2 and svc.captured_total() == 2900
    assert [e["kind"] for e in svc.events].count("idempotent_replay") == 1


def test_idempotency_conflict(svc, mandate, token):
    pay(svc, mandate, token, ik="dup")
    assert code(lambda: pay(svc, mandate, token, c=cart(3000), ik="dup")) == "idempotency_conflict"


@pytest.mark.parametrize("k", ["", "x" * 129])
def test_bad_idempotency_key(svc, mandate, token, k):
    assert code(lambda: pay(svc, mandate, token, ik=k)) == "idempotency_key"


def test_replay_of_approval_blocked(svc, mandate, token):
    c = cart()
    ap = approve_cart(KEY, mandate, c, NOW)
    submit(svc, mandate, c, ap, token, "a")
    assert code(lambda: submit(svc, mandate, c, ap, token, "b")) == "replay"
    assert svc.captured_total() == 2900


def test_cart_swapped_after_approval(svc, mandate, token):
    ap = approve_cart(KEY, mandate, cart(), NOW)
    assert code(lambda: submit(svc, mandate, cart(9000), ap, token, "a")) == "cart_changed"
    assert svc.captured_total() == 0


def test_approval_for_other_mandate(svc, mandate, token):
    other = new_mandate(KEY, "u1", 10000, 20000, ["fernhill-market"], NOW)
    ap = approve_cart(KEY, other, cart(), NOW)
    assert code(lambda: submit(svc, mandate, cart(), ap, token, "a")) == "mismatch"


def test_merchant_not_allowlisted(svc, mandate, token):
    assert code(lambda: pay(svc, mandate, token, c=cart(merchant="shady-shop"))) == "merchant"


def test_per_purchase_cap(svc, mandate, token):
    assert code(lambda: pay(svc, mandate, token, c=cart(10001))) == "cap"
    pay(svc, mandate, token, c=cart(10000))


def test_total_cap(svc, mandate, token):
    pay(svc, mandate, token, c=cart(10000), ik="1")
    pay(svc, mandate, token, c=cart(9000), ik="2")
    assert code(lambda: pay(svc, mandate, token, c=cart(1001), ik="3")) == "cap"


def test_empty_cart(svc, mandate, token):
    assert code(lambda: pay(svc, mandate, token, c=cart(0))) == "empty"


def test_expired_mandate(svc, mandate, token, clock):
    clock.t = NOW + 4000
    assert code(lambda: pay(svc, mandate, token, now=clock.t)) == "auth"


def test_expired_approval(svc, mandate, token, clock):
    clock.t = NOW + 301
    assert code(lambda: pay(svc, mandate, token, now=NOW)) == "auth"


def test_forged_mandate(svc, mandate, token):
    fake = replace(mandate, per_purchase_cap=1_000_000, total_cap=1_000_000)
    assert code(lambda: pay(svc, fake, token)) == "auth"


def test_fraud_velocity(clock, mandate):
    s = PaymentService(user_key=KEY, clock=clock, fraud=FraudPolicy(max_checkouts_per_window=2, min_history_for_spike=99))
    t = s.tokenize("4242424242424242")
    pay(s, mandate, t, ik="1")
    pay(s, mandate, t, ik="2")
    assert code(lambda: pay(s, mandate, t, ik="3")) == "fraud_velocity"
    clock.t += 61
    pay(s, mandate, t, ik="4", now=clock.t)


def test_fraud_spike(clock):
    m = new_mandate(KEY, "u1", 100000, 1000000, ["fernhill-market"], NOW)
    s = PaymentService(user_key=KEY, clock=clock, fraud=FraudPolicy(max_checkouts_per_window=100))
    t = s.tokenize("4242424242424242")
    for i in range(3):
        pay(s, m, t, c=cart(1000), ik=str(i))
    assert code(lambda: pay(s, m, t, c=cart(5001), ik="big")) == "fraud_spike"
    pay(s, m, t, c=cart(5000), ik="ok")


@pytest.mark.parametrize("field", ["max_checkouts_per_window", "window_s", "spike_multiple", "min_history_for_spike"])
def test_fraud_policy_rejects_negative(field):
    with pytest.raises(SafeError, match="non-negative"):
        FraudPolicy(**{field: -1})


def test_refund_partial_full_and_over(svc, mandate, token):
    r = pay(svc, mandate, token)
    assert svc.refund(r.payment_id, 900, "r1").status == "partially_refunded"
    assert code(lambda: svc.refund(r.payment_id, 2001, "r2")) == "refund_too_large"
    assert svc.refund(r.payment_id, 2000, "r3").status == "refunded"
    assert svc.captured_total() == 0


def test_refund_idempotent_and_conflict(svc, mandate, token):
    r = pay(svc, mandate, token)
    svc.refund(r.payment_id, 500, "r")
    svc.refund(r.payment_id, 500, "r")
    assert r.refunded == 500
    assert code(lambda: svc.refund(r.payment_id, 600, "r")) == "idempotency_conflict"


@pytest.mark.parametrize("amt", [-1, -2900, 1.5])
def test_refund_negative_rejected(svc, mandate, token, amt):
    r = pay(svc, mandate, token)
    with pytest.raises(SafeError):
        svc.refund(r.payment_id, amt, "r")
    assert r.refunded == 0


def test_refund_zero_and_unknown(svc, mandate, token):
    r = pay(svc, mandate, token)
    assert code(lambda: svc.refund(r.payment_id, 0, "z")) == "refund_zero"
    assert code(lambda: svc.refund("pay_x", 1, "z")) == "unknown_payment"


def test_refund_restores_mandate_budget(svc, mandate, token):
    r = pay(svc, mandate, token, c=cart(10000), ik="1")
    pay(svc, mandate, token, c=cart(10000), ik="2")
    svc.refund(r.payment_id, 10000, "r")
    pay(svc, mandate, token, c=cart(10000), ik="3")
