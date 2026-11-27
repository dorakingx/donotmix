"""Discount engine: 10% off orders whose subtotal reaches the threshold."""

from .flags import Features

THRESHOLD = 5000
PERCENT = 10


def discount_for(priced_lines, features: Features) -> int:
    """priced_lines: iterable of (sku, qty, unit_price)."""
    if not features.on("discount_engine"):
        return 0
    subtotal = sum(q * u for _, q, u in priced_lines)
    return subtotal * PERCENT // 100 if subtotal >= THRESHOLD else 0
