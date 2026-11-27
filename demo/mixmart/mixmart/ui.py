"""Presentation helpers. dark_mode only changes colours; it is read next to many other flags."""

from .flags import Features


def theme(features: Features) -> dict:
    dark = features.on("dark_mode")
    return {
        "bg": "#111111" if dark else "#fafaf5",
        "fg": "#f5f1e6" if dark else "#111111",
        "badges": [n for n in ("new_checkout", "express_shipping", "loyalty_program", "ai_search") if features.on(n)],
    }
