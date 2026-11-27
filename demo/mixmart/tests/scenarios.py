"""Business scenarios shared by the storefront suite and the 'safe alone' suite."""

from mixmart import checkout as co
from mixmart.catalog import CATALOG
from mixmart.recommend import also_bought
from mixmart.search import search
from mixmart.ui import theme

from spec import expected_order

BIG_CART = {"MM-102": 2, "MM-105": 1}      # crosses the discount threshold, has high-demand items
SMALL_CART = {"MM-101": 1, "MM-104": 1}


def _check(order, exp):
    assert order.subtotal == exp["subtotal"], f"subtotal {order.subtotal} != {exp['subtotal']}"
    assert order.discount == exp["discount"], f"discount {order.discount} != {exp['discount']}"
    assert order.total == exp["total"], f"total {order.total} != expected {exp['total']}"
    assert order.payment["amount"] == order.total, "payment amount differs from order total"


def member_checkout(features):
    for cart in (BIG_CART, SMALL_CART):
        order = co.checkout(cart, co.Customer("c-1"), features)
        _check(order, expected_order(cart, set(features.enabled_flags), member=True))


def express_checkout(features):
    if not features.on("express_shipping"):
        return
    order = co.checkout(SMALL_CART, co.Customer("c-2"), features, shipping_choice="express")
    _check(order, expected_order(SMALL_CART, set(features.enabled_flags), member=True, shipping_choice="express"))


def guest_checkout(features):
    if not features.on("guest_checkout"):
        try:
            co.checkout(SMALL_CART, None, features)
        except co.CheckoutError as e:
            assert "login" in str(e)
            return
        raise AssertionError("guest checkout must be rejected when guest_checkout is off")
    order = co.checkout(SMALL_CART, None, features)
    _check(order, expected_order(SMALL_CART, set(features.enabled_flags), member=False))


def loyalty_points(features):
    cust = co.Customer("c-3")
    order = co.checkout(BIG_CART, cust, features)
    exp = expected_order(BIG_CART, set(features.enabled_flags), member=True)
    assert order.points_awarded == exp["points"], f"points {order.points_awarded} != {exp['points']}"
    assert cust.points == exp["points"]


def recommendations(features):
    recs = also_bought("MM-101", features)
    if not features.on("recommendation"):
        assert recs == []
        return
    assert recs, "recommendation widget is empty for a product with related items"
    assert all(r in CATALOG for r in recs)


def product_search(features):
    hits = {h.upper() for h in search("gloves", features)}  # SKUs are case-insensitive in search results
    assert "MM-102" in hits


def ui_theme(features):
    t = theme(features)
    assert t["bg"] != t["fg"]


ALL = [member_checkout, express_checkout, guest_checkout, loyalty_points, recommendations, product_search, ui_theme]
