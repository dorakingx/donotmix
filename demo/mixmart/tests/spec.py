"""Independent reference model of MixMart's business rules (the test oracle).

Written from the product spec, not from the implementation. Flags only select which rules apply.
"""

LIST = {"MM-101": 2400, "MM-102": 1800, "MM-103": 3200, "MM-104": 900, "MM-105": 5600}
HIGH_DEMAND = {"MM-102", "MM-104"}


def expected_order(cart: dict, flags: set, member: bool, shipping_choice: str = "standard") -> dict:
    def price(sku):
        p = LIST[sku]
        return p * 120 // 100 if ("dynamic_pricing" in flags and sku in HIGH_DEMAND) else p

    subtotal = sum(price(s) * q for s, q in cart.items())
    # Rule D1: the discount is computed on the prices the customer is actually charged.
    discount = subtotal * 10 // 100 if ("discount_engine" in flags and subtotal >= 5000) else 0
    express = "express_shipping" in flags and "new_checkout" in flags and shipping_choice == "express"
    fee = 1500 if express else 500
    total = subtotal - discount + fee
    points = total // 100 if ("loyalty_program" in flags and member) else 0
    return {"subtotal": subtotal, "discount": discount, "fee": fee, "total": total, "points": points}
