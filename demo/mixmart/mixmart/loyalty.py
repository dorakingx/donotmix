"""Loyalty program: 1 point per full 100 cents paid."""

from .flags import Features


def award_points(customer, total: int, features: Features) -> int:
    if not features.on("loyalty_program"):
        return 0
    points = total // 100
    customer.points += points  # customer is an account; see checkout for who can reach here
    return points
