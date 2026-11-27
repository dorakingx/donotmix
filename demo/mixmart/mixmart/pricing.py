"""Unit prices. dynamic_pricing applies a demand surcharge to high-demand products."""

from .catalog import Product
from .flags import Features

SURGE_PERCENT = 20


def unit_price(product: Product, features: Features) -> int:
    if features.on("dynamic_pricing") and product.high_demand:
        return product.list_price * (100 + SURGE_PERCENT) // 100
    return product.list_price


def quote_lines(lines, features: Features) -> list[tuple[str, int, int]]:
    """(sku, qty, unit_price_charged) for each cart line."""
    return [(p.sku, qty, unit_price(p, features)) for p, qty in lines]
