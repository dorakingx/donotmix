"""A tiny in-process 'application' for engine tests (python runner)."""


def run(states, persona):
    out = []
    if states["new_checkout"] and states["dynamic_pricing"] and states["discount_engine"]:
        out.append(("test_total", "AssertionError", "total mismatch"))
    if states["guest_checkout"] and states["loyalty_program"]:
        out.append(("test_guest", "AttributeError", "'NoneType' object has no attribute 'points'"))
    return out
