import pytest

from agentcommerce.mandate import Cart, Line, approve_cart, new_mandate
from agentcommerce.payments import FraudPolicy, PaymentService

KEY = b"k" * 32
NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(clock):
    return PaymentService(user_key=KEY, clock=clock, fraud=FraudPolicy(max_checkouts_per_window=100))


@pytest.fixture
def mandate():
    return new_mandate(KEY, "u1", 10000, 20000, ["fernhill-market"], NOW)


@pytest.fixture
def token(svc):
    return svc.tokenize("4242 4242 4242 4242")


def cart(price=2900, qty=1, merchant="fernhill-market"):
    return Cart(merchant, (Line("SKU-1", "headlamp", price, qty),))


def submit(svc, mandate, c, approval, tok, ik):
    return svc.checkout(mandate=mandate, cart=c, approval=approval, card_token=tok, idempotency_key=ik)


def pay(svc, mandate, tok, c=None, ik="k1", now=NOW):
    c = c or cart()
    return submit(svc, mandate, c, approve_cart(KEY, mandate, c, now), tok, ik)
