import math
from dataclasses import replace

import pytest
from conftest import KEY, NOW, cart

from agentcommerce.mandate import Cart, Line, approve_cart, new_mandate
from agentcommerce.money import MAX_CENTS, cents, fmt
from agentcommerce.pii import SafeError


@pytest.mark.parametrize("v", [-1, -0.5, -10_000, -MAX_CENTS])
def test_negative_amounts_rejected(v):
    with pytest.raises(SafeError, match="negative|whole"):
        cents(v)


@pytest.mark.parametrize("v", [1.5, math.nan, math.inf, -math.inf, "100", None, True, False, [1]])
def test_non_integer_amounts_rejected(v):
    with pytest.raises(SafeError):
        cents(v)


def test_ceiling():
    assert cents(MAX_CENTS) == MAX_CENTS
    with pytest.raises(SafeError, match="ceiling"):
        cents(MAX_CENTS + 1)


def test_whole_float_ok_and_fmt():
    assert cents(250.0) == 250
    assert fmt(0) == "$0.00" and fmt(2905) == "$29.05"


@pytest.mark.parametrize("per,total", [(-1, 100), (100, -1), (-5, -5)])
def test_negative_budget_rejected(per, total):
    with pytest.raises(SafeError, match="negative"):
        new_mandate(KEY, "u", per, total, ["m"], NOW)


def test_per_above_total_rejected():
    with pytest.raises(SafeError, match="exceed"):
        new_mandate(KEY, "u", 200, 100, ["m"], NOW)


@pytest.mark.parametrize("ttl", [0, -1])
def test_bad_ttl(ttl):
    with pytest.raises(SafeError, match="ttl"):
        new_mandate(KEY, "u", 1, 1, ["m"], NOW, ttl_s=ttl)


def test_empty_allowlist():
    with pytest.raises(SafeError, match="allowlist"):
        new_mandate(KEY, "u", 1, 1, [], NOW)


def test_mandate_verifies_and_expires(mandate):
    mandate.verify(KEY, NOW + 10)
    with pytest.raises(SafeError, match="expired"):
        mandate.verify(KEY, NOW + 3600)


@pytest.mark.parametrize(
    "field,val",
    [
        ("per_purchase_cap", 99999),
        ("total_cap", 999999),
        ("merchants", ("evil",)),
        ("expires_at", NOW + 1e9),
        ("user_id", "other"),
    ],
)
def test_tampered_mandate_fails(mandate, field, val):
    with pytest.raises(SafeError, match="signature"):
        replace(mandate, **{field: val}).verify(KEY, NOW)


def test_wrong_key_fails(mandate):
    with pytest.raises(SafeError, match="signature"):
        mandate.verify(b"x" * 32, NOW)


@pytest.mark.parametrize("q", [0, -1, 1.5, True])
def test_bad_quantity(q):
    with pytest.raises(SafeError, match="quantity"):
        Line("s", "n", 100, q)


def test_negative_unit_price():
    with pytest.raises(SafeError):
        Line("s", "n", -100, 1)


def test_cart_total_and_digest_changes():
    a = Cart("m", (Line("s", "n", 100, 2),))
    assert a.total == 200
    assert a.digest() != Cart("m", (Line("s", "n", 100, 3),)).digest()
    assert a.digest() != Cart("m2", a.lines).digest()


def test_approval_expiry_and_tamper(mandate):
    ap = approve_cart(KEY, mandate, cart(), NOW, ttl_s=60)
    ap.verify(KEY, NOW + 59)
    with pytest.raises(SafeError, match="expired"):
        ap.verify(KEY, NOW + 60)
    with pytest.raises(SafeError, match="signature"):
        replace(ap, amount=1).verify(KEY, NOW)


def test_nonces_unique(mandate):
    assert len({approve_cart(KEY, mandate, cart(), NOW).nonce for _ in range(50)}) == 50
