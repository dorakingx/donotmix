"""Storefront business-invariant suite. DoNotMix runs this suite against candidate flag states."""

import scenarios as sc


def test_member_checkout_total(features):
    sc.member_checkout(features)


def test_express_checkout(features):
    sc.express_checkout(features)


def test_guest_checkout_policy(features):
    sc.guest_checkout(features)


def test_loyalty_points(features):
    sc.loyalty_points(features)


def test_recommendations(features):
    sc.recommendations(features)


def test_product_search(features):
    sc.product_search(features)


def test_ui_theme(features):
    sc.ui_theme(features)
