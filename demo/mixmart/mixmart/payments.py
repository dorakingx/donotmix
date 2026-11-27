"""Payment intents. new_payment talks to the (simulated) new gateway, which validates more fields."""

from .flags import Features


class PaymentError(ValueError):
    pass


def create_intent(amount: int, shipping_method: str | None, features: Features) -> dict:
    if amount <= 0:
        raise PaymentError("amount must be positive")
    if features.on("new_payment"):
        if not shipping_method:
            raise PaymentError("new gateway requires shipping_method")
        return {"gateway": "nova", "amount": amount, "currency": "USD", "shipping_method": shipping_method}
    return {"gateway": "legacy", "amount": amount}
