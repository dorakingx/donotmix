import json

from donotmix.ai import LLMAnalyst, RuleAnalyst, make_analyst
from donotmix.config import AICfg

H = {"id": "HZ-1", "core": {"a": True, "b": True}, "personas": ["p"], "generality": {"verdict": "sufficient", "checked": 8, "failed": 8},
     "atoms": [{"persona": "p", "test": "t::test_total", "kind": "AssertionError", "message": "total 1 != 2"}],
     "code_refs": [{"file": "x.py", "line": 3, "scope": "f", "flag": "a", "snippet": "  3 | if on('a')"}], "failure_class": "silent-wrong-result",
     "repro": {"donotmix": "donotmix reproduce HZ-1"}}


class FakeLLM(LLMAnalyst):
    provider = "fake"

    def __init__(self, cfg, reply=None, fail=False):
        super().__init__(cfg)
        self.reply, self.fail, self.prompts = reply, fail, []

    def _call(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise ConnectionError("network down")
        self.input_tokens += len(prompt) // 4
        self.output_tokens += 50
        return self.reply


def test_rule_based_is_default_and_labelled():
    a = make_analyst(AICfg())
    assert isinstance(a, RuleAnalyst) and a.usage()["llm_calls"] == 0
    ex = a.explain(H, {"flags": []})
    assert ex["mode"] == "rule-based" and any("necessary" in v for v in ex["verified"])


def test_llm_failure_falls_back_to_rules():
    a = FakeLLM(AICfg(), fail=True)
    ex = a.explain(H, {"flags": []})
    assert "llm" not in ex and ex["verified"]
    assert a.failures == 1 and "fallback" in a.usage()["notes"][0]


def test_llm_output_is_marked_unverified_and_checked_against_evidence():
    reply = "```json\n" + json.dumps({"root_cause_hypothesis": "discount on list price", "explanation": "e", "suspect_locations": ["x.py:3", "y.py:9"],
                                       "fix_suggestions": ["use charged price"], "confidence": "medium"}) + "\n```"
    a = FakeLLM(AICfg(), reply=reply)
    ex = a.explain(H, {"flags": []})
    assert ex["llm"]["status"] == "unverified-hypothesis"
    assert [s["matches_static_evidence"] for s in ex["llm"]["suspect_locations"]] == [True, False]
    assert "verified_facts" in a.prompts[0] and "document" not in a.prompts[0]


def test_llm_call_budget_enforced():
    a = FakeLLM(AICfg(max_calls=1), reply=json.dumps({"root_cause_hypothesis": "x"}))
    a.explain(H, {"flags": []})
    ex2 = a.explain({**H, "id": "HZ-2"}, {"flags": []})
    assert len(a.prompts) == 1 and "llm" not in ex2 and "budget" in " ".join(a.usage()["notes"])


def test_prompt_respects_input_budget():
    big = {**H, "code_refs": [{**H["code_refs"][0], "snippet": "x" * 100000}] * 6}
    a = FakeLLM(AICfg(max_input_tokens=1000), reply="{}")
    a.explain(big, {"flags": []})
    assert len(a.prompts[0]) <= 4000
