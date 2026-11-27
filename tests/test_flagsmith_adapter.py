import json
import subprocess

import pytest

from donotmix.approval import ApprovalToken
from donotmix.flagsmith import FlagsmithAuthError, FlagsmithCLI, FlagsmithError, FlagsmithUnavailable, apply_config_to_document


class FakeRun:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        for key, (code, out, err) in self.responses.items():
            if key in " ".join(argv):
                return subprocess.CompletedProcess(argv, code, out, err)
        return subprocess.CompletedProcess(argv, 0, "{}", "")


def test_json_parsing_and_project_flag():
    fake = FakeRun({"flag list": (0, json.dumps([{"feature": "a", "enabled": True}]), "")})
    cli = FlagsmithCLI(project="P", api_url="http://x", runner=fake)
    assert cli.flag_list("Staging") == [{"feature": "a", "enabled": True}]
    argv = fake.calls[0]
    assert argv[:2] == ["flagsmith", "--no-input"] and "--json" in argv and "--project" in argv


@pytest.mark.parametrize("err,exc", [
    ("Error: not logged in", FlagsmithAuthError),
    ("Error: FLAGSMITH_API_KEY_x is not a Master API key", FlagsmithAuthError),
    ("dial tcp 127.0.0.1:8999: connect: connection refused", FlagsmithUnavailable),
    ("Error: something else", FlagsmithError),
])
def test_error_classification(err, exc):
    cli = FlagsmithCLI(runner=FakeRun({"flag list": (1, "", err)}))
    with pytest.raises(exc):
        cli.flag_list("e")


def test_non_json_output_is_error():
    cli = FlagsmithCLI(runner=FakeRun({"flag list": (0, "not json", "")}))
    with pytest.raises(FlagsmithError):
        cli.flag_list("e")


def test_write_requires_approval_token():
    fake = FakeRun({})
    cli = FlagsmithCLI(runner=fake)
    with pytest.raises(PermissionError):
        cli._set_enabled("a", True, "env", None)
    with pytest.raises(PermissionError):
        cli._set_enabled("b", True, "env", ApprovalToken("P1", "env", frozenset({"a"})))
    assert not any("enable" in " ".join(c) for c in fake.calls)
    cli._set_enabled("a", True, "env", ApprovalToken("P1", "env", frozenset({"a"})))
    assert any(c[2:5] == ["flag", "enable", "a"] for c in fake.calls)


def test_apply_config_to_document_keeps_overrides():
    doc = {"feature_states": [{"feature": {"name": "a"}, "enabled": False}, {"feature": {"name": "b"}, "enabled": True}],
           "project": {"segments": [{"name": "s", "feature_states": [{"feature": {"name": "a"}, "enabled": True}]}]}}
    out = apply_config_to_document(doc, {"a": True, "b": False})
    assert [fs["enabled"] for fs in out["feature_states"]] == [True, False]
    assert out["project"]["segments"] == doc["project"]["segments"]
    assert doc["feature_states"][0]["enabled"] is False  # original untouched
    with pytest.raises(KeyError):
        apply_config_to_document(doc, {"zzz": True})
