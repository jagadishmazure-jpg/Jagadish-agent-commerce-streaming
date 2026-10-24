import logging

import pytest
from conftest import KEY, NOW, cart, submit

from agentcommerce.mandate import approve_cart
from agentcommerce.pii import SafeError, get_logger, has_pii, scrub
from agentcommerce.x402 import PaywalledResource, pay_quote

CARDS = ["4242 4242 4242 4242", "4242-4242-4242-4242", "4242424242424242", "5555555555554444", "378282246310005"]


@pytest.mark.parametrize("c", CARDS)
def test_cards_masked(c):
    assert scrub(f"card {c} ok") == "card [CARD] ok"


@pytest.mark.parametrize(
    "text,masked",
    [
        ("mail a.b+c@example.org now", "[EMAIL]"),
        ("call (555) 010-2030", "[PHONE]"),
        ("call +1 555-010-2030", "[PHONE]"),
        ("ssn 123-45-6789", "[SSN]"),
    ],
)
def test_other_pii_masked(text, masked):
    assert masked in scrub(text)


@pytest.mark.parametrize("text", ["order 1234567890123", "ssn 000-12-3456", "ssn 666-12-3456", "ssn 123-00-4567", "total $29.00"])
def test_non_pii_left_alone(text):
    assert not has_pii(text)


def test_safe_error_scrubs():
    assert "4242" not in str(SafeError("bad card 4242 4242 4242 4242 for a@b.co"))


def test_logger_scrubs_args(caplog):
    log = get_logger("agentcommerce.test")
    with caplog.at_level(logging.INFO, logger="agentcommerce.test"):
        log.info("user %s paid with %s", "x@y.com", "4242424242424242")
    assert "[EMAIL]" in caplog.text and "[CARD]" in caplog.text and "4242424242424242" not in caplog.text


def test_payment_logs_never_contain_card(svc, mandate, caplog):
    with caplog.at_level(logging.DEBUG):
        t = svc.tokenize("4242 4242 4242 4242")
        c = cart()
        submit(svc, mandate, c, approve_cart(KEY, mandate, c, NOW), t, "k")
        with pytest.raises(SafeError):
            svc.tokenize("4242 4242 4242 4241")
    assert "4242 4242" not in caplog.text and "424242424242" not in caplog.text
    assert all("4242424242424242" not in str(e) for e in svc.events)


def paywall():
    return PaywalledResource(KEY, "larkspur-books", 25, "/r")


def test_x402_happy_and_single_use():
    p = paywall()
    proof = pay_quote(KEY, p.quote(NOW), {"larkspur-books"}, 100)
    assert p.redeem(proof, NOW) == "contents of /r"
    with pytest.raises(SafeError, match="already used"):
        p.redeem(proof, NOW)


def test_x402_expired():
    p = paywall()
    q = p.quote(NOW, ttl_s=10)
    with pytest.raises(SafeError, match="expired"):
        p.redeem(pay_quote(KEY, q, {"larkspur-books"}, 100), NOW + 10)


def test_x402_forged_and_tampered():
    p = paywall()
    q = p.quote(NOW)
    proof = pay_quote(KEY, q, {"larkspur-books"}, 100)
    with pytest.raises(SafeError, match="signature"):
        p.redeem({**proof, "amount": 1}, NOW)
    with pytest.raises(SafeError, match="signature"):
        p.redeem(pay_quote(b"z" * 32, q, {"larkspur-books"}, 100), NOW)
    with pytest.raises(SafeError, match="signature"):
        p.redeem({}, NOW)


def test_x402_unknown_quote():
    p, other = paywall(), paywall()
    with pytest.raises(SafeError, match="unknown"):
        p.redeem(pay_quote(KEY, other.quote(NOW), {"larkspur-books"}, 100), NOW)


def test_x402_agent_refuses_outside_mandate():
    q = paywall().quote(NOW)
    with pytest.raises(SafeError, match="allowlist"):
        pay_quote(KEY, q, {"fernhill-market"}, 100)
    with pytest.raises(SafeError, match="cap"):
        pay_quote(KEY, q, {"larkspur-books"}, 24)
    with pytest.raises(SafeError, match="negative"):
        pay_quote(KEY, q, {"larkspur-books"}, -1)


def test_x402_negative_price():
    with pytest.raises(SafeError, match="negative"):
        PaywalledResource(KEY, "m", -5, "/r")
