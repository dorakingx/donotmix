"""Checkout flows: legacy and new_checkout."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import discounts, loyalty, payments, pricing, shipping
from .catalog import get
from .flags import Features


class CheckoutError(Exception):
    pass


@dataclass
class Customer:
    id: str
    points: int = 0


@dataclass
class Order:
    subtotal: int
    discount: int
    shipping_fee: int
    total: int
    shipping_method: str | None
    payment: dict
    points_awarded: int = 0
    lines: list = field(default_factory=list)


def _lines(cart: dict[str, int]):
    out = []
    for sku, qty in cart.items():
        p = get(sku)
        if p is None or qty <= 0:
            raise CheckoutError(f"bad cart line {sku}")
        out.append((p, qty))
    return out


def checkout(cart: dict[str, int], customer: Customer | None, features: Features, shipping_choice: str = "standard") -> Order:
    if customer is None and not features.on("guest_checkout"):
        raise CheckoutError("login required")
    if features.on("new_checkout"):
        return _new_checkout(cart, customer, features, shipping_choice)
    return _legacy_checkout(cart, customer, features)


def _legacy_checkout(cart, customer, features: Features) -> Order:
    priced = pricing.quote_lines(_lines(cart), features)
    subtotal = sum(q * u for _, q, u in priced)
    discount = discounts.discount_for(priced, features)
    method, fee = shipping.select("standard", features)
    total = subtotal - discount + fee
    intent = payments.create_intent(total, method or "standard", features)
    pts = loyalty.award_points(customer, total, features)
    return Order(subtotal, discount, fee, total, method, intent, pts, priced)


def _new_checkout(cart, customer, features: Features, shipping_choice: str) -> Order:
    lines = _lines(cart)
    priced = pricing.quote_lines(lines, features)
    subtotal = sum(q * u for _, q, u in priced)
    # The new checkout pre-computes the discount from catalog prices so it can be shown in the
    # cart preview before repricing happens.
    discount = discounts.discount_for([(p.sku, q, p.list_price) for p, q in lines], features)
    method, fee = shipping.select(shipping_choice, features)
    total = subtotal - discount + fee
    intent = payments.create_intent(total, method, features)
    pts = loyalty.award_points(customer, total, features)
    return Order(subtotal, discount, fee, total, method, intent, pts, priced)
