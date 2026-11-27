"""CommandRunner against the real MixMart pytest suite and the Flagsmith SDK in offline mode."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("flagsmith")

from donotmix.runner import CommandRunner, Persona

ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads((ROOT / "demo/mixmart/fixtures/environment.all_off.json").read_text())
NAMES = [fs["feature"]["name"] for fs in DOC["feature_states"]]
CMD = f"{sys.executable} -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider --junitxml={{junit}}"


def cfg(on):
    return tuple(n in on for n in NAMES)


def test_single_flag_passes_and_combination_fails(tmp_path):
    r = CommandRunner(CMD, DOC, tmp_path, cwd=str(ROOT))
    p = Persona("shopper", "shopper-1", (("beta", False),))
    assert r.run(NAMES, cfg({"discount_engine"}), p).passed
    ex = r.run(NAMES, cfg({"new_checkout", "dynamic_pricing", "discount_engine"}), p)
    assert not ex.passed
    assert any(a.test.endswith("test_member_checkout_total") and a.kind == "AssertionError" for a in ex.atoms)
    ex2 = r.run(NAMES, cfg({"guest_checkout", "loyalty_program"}), p)
    assert any(a.kind == "AttributeError" for a in ex2.atoms)
    assert r.run(NAMES, cfg({"discount_engine"}), p) is r.cache[(cfg({"discount_engine"}), "shopper")]
