"""Shipping options."""

from .flags import Features

STANDARD_FEE, EXPRESS_FEE = 500, 1500


def select(requested: str, features: Features) -> tuple[str | None, int]:
    """Return (method_name, fee). Method names were introduced together with express shipping."""
    if features.on("express_shipping"):
        if requested == "express":
            return "express", EXPRESS_FEE
        return "standard", STANDARD_FEE
    # legacy path: one implicit method, unnamed
    return None, STANDARD_FEE
