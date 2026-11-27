"""'Safe alone': every flag, switched on by itself, passes every scenario.

Builds environment documents from the bundled all-off fixture and evaluates them with the
Flagsmith SDK in offline mode. Run with:  pytest demo/mixmart/tests/test_safe_alone.py
"""

import copy
import json
from pathlib import Path

import pytest

import scenarios as sc
from mixmart.flags import FLAG_NAMES, features_for

FIXTURE = Path(__file__).parent.parent / "fixtures" / "environment.all_off.json"


@pytest.mark.parametrize("flag", FLAG_NAMES)
@pytest.mark.parametrize("scenario", sc.ALL, ids=lambda f: f.__name__)
def test_flag_alone(flag, scenario, tmp_path):
    doc = json.loads(FIXTURE.read_text())
    doc = copy.deepcopy(doc)
    for fs in doc["feature_states"]:
        fs["enabled"] = fs["feature"]["name"] == flag
    path = tmp_path / "env.json"
    path.write_text(json.dumps(doc))
    scenario(features_for("shopper-1", {"beta": False}, document_path=str(path)))
