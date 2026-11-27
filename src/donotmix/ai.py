"""Explanations and fix suggestions for hazards.

Facts (what fails, the minimal condition, how general it is) always come from executed
tests. Analysts only *explain* and *suggest*; their output is labelled with its mode and
kept separate from verified results. LLM use is opt-in, budget-capped and degrades to the
deterministic rule-based analyst when unavailable or over budget.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .config import AICfg


def _lit(k, v) -> str:
    return f"{k}={'on' if v is True else 'off' if v is False else v}"


class RuleAnalyst:
    mode = "rule-based"

    def __init__(self) -> None:
        self.calls = 0
        self.notes: list[str] = []

    def usage(self) -> dict:
        return {"provider": "rule", "mode": self.mode, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "notes": self.notes,
                "label": "Deterministic rule-based analysis (no LLM used)."}

    def explain(self, h: dict, report: dict) -> dict:
        core = h.get("core", {})
        if not core:
            return {"mode": self.mode, "verified": [], "explanation": "Failure could not be minimized (no passing reference).", "fix_suggestions": []}
        g = h.get("generality", {})
        lits = ", ".join(_lit(k, v) for k, v in core.items())
        tests = sorted({a["test"].split("::")[-1] for a in h.get("atoms", [])})
        verified = [
            (f"Fails when {lits} hold together" if len(core) > 1 else f"Fails whenever {lits}") + f" (persona: {', '.join(h.get('personas', []))}).",
            f"Each of these {len(core)} literal(s) is necessary: flipping any one of them makes the failing test pass.",
        ]
        if g.get("verdict") == "sufficient":
            verified.append(f"Sufficient: the failure occurs in all {g['checked']} feasible completions of the other flags.")
        elif g.get("verdict") == "held-in-sample":
            verified.append(f"Held in all {g['checked']} sampled completions of the other flags (sample, not exhaustive).")
        elif g.get("verdict") == "context-dependent":
            verified.append(f"Context-dependent: {g['passed']} of {g['checked']} completions pass, so other flags also matter.")
        if h.get("pinned"):
            verified.append("Implied by constraints: " + ", ".join(f"{k} (constraint {', '.join(v['by'])})" for k, v in h["pinned"].items()) + ".")
        for flag, val in (h.get("overrides_applied") or {}).items():
            segs = next((f.get("segment_scoped") for f in report.get("flags", []) if f["name"] == flag), None)
            verified.append(f"For persona {h['personas'][0]}, Flagsmith evaluates {flag}={'on' if val else 'off'} despite the environment default"
                            + (f" (segment override: {', '.join(segs)})." if segs else " (override)."))
        msg = next((a.get("message") for a in h.get("atoms", []) if a.get("message")), "")
        reasoning = []
        ev = h.get("dependency_evidence", [])
        if ev:
            kinds = sorted({e["kind"] for e in ev})
            reasoning.append("Static analysis links these flags via " + ", ".join(kinds) + " edges, e.g. " + "; ".join(f"{e['where']} ({e['detail'] or e['kind']})" for e in ev[:2]) + ".")
        elif len(core) > 1:
            reasoning.append("Static analysis found no shared scope for these flags; the interaction is indirect (data flow / call chain).")
        cls = h.get("failure_class")
        if cls == "silent-wrong-result":
            reasoning.append("No exception is raised: the system returns a wrong business result, which per-flag tests did not catch.")
        else:
            reasoning.append(f"The combination raises {', '.join(sorted({a['kind'] for a in h.get('atoms', [])}))}.")
        fixes = [
            f"Add a regression test pinned to this combination: `{h.get('repro', {}).get('donotmix', 'donotmix reproduce ' + h.get('id', ''))}`.",
            "Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint "
            + "`!(" + " & ".join(k if v is True else f"!{k}" if v is False else f'{k} == "{v}"' for k, v in core.items()) + ")` in donotmix.yaml.",
        ]
        if h.get("code_refs"):
            r = h["code_refs"][0]
            fixes.append(f"Start at {r['file']}:{r['line']} ({r['scope']}): check every place where the code paths of {', '.join(core)} read or write shared state.")
        return {"mode": self.mode, "verified": verified, "observed_error": msg[:300], "reasoning": reasoning, "fix_suggestions": fixes,
                "label": "verified = backed by executed tests; reasoning = deterministic heuristics"}

    def hypothesize(self, report: dict) -> list[dict]:
        return []


SYSTEM = (
    "You are DoNotMix's root-cause assistant. You receive VERIFIED facts about a failure caused by a "
    "combination of feature flags (all facts come from executed tests) plus code snippets. Explain the likely "
    "root cause and propose concrete code fixes. Do not restate facts as if you discovered them; do not claim "
    "anything is verified. Reply with ONLY a JSON object: {\"root_cause_hypothesis\": str, \"explanation\": str, "
    "\"suspect_locations\": [\"file:line\"], \"fix_suggestions\": [str], \"confidence\": \"low|medium|high\"}"
)


class LLMAnalyst(RuleAnalyst):
    """Shared budget logic for LLM-backed analysts. Falls back to RuleAnalyst output."""

    provider = "llm"

    def __init__(self, cfg: AICfg) -> None:
        super().__init__()
        self.cfg = cfg
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.failures = 0

    @property
    def mode(self) -> str:  # type: ignore[override]
        return f"llm:{self.provider}"

    def usage(self) -> dict:
        return {"provider": self.provider, "mode": self.mode, "model": self.cfg.model, "llm_calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": round(self.cost_usd, 4), "budget": {"max_calls": self.cfg.max_calls, "max_input_tokens": self.cfg.max_input_tokens},
                "failures": self.failures, "notes": self.notes,
                "label": "LLM output is an unverified hypothesis; verified facts come from executed tests."}

    def _prompt(self, h: dict, base: dict) -> str:
        ctx = {
            "verified_facts": base["verified"],
            "observed_error": base.get("observed_error", ""),
            "failing_tests": sorted({a["test"] for a in h.get("atoms", [])}),
            "code": [{"file": r["file"], "line": r["line"], "flag": r["flag"], "snippet": r["snippet"]} for r in h.get("code_refs", [])[:6]],
            "static_dependency_evidence": h.get("dependency_evidence", [])[:4],
        }
        text = json.dumps(ctx, indent=0)
        limit = self.cfg.max_input_tokens * 4  # ~4 chars per token
        return text[:limit]

    def _call(self, prompt: str) -> str:  # pragma: no cover - provider specific
        raise NotImplementedError

    def explain(self, h: dict, report: dict) -> dict:
        base = super().explain(h, report)
        if not h.get("core"):
            return base
        if self.calls >= self.cfg.max_calls:
            self.notes.append(f"{h.get('id')}: LLM call budget exhausted, rule-based only")
            return base
        if self.input_tokens >= self.cfg.max_input_tokens * self.cfg.max_calls:
            self.notes.append(f"{h.get('id')}: LLM token budget exhausted, rule-based only")
            return base
        try:
            raw = self._call(self._prompt(h, base))
            self.calls += 1
            data = _extract_json(raw)
        except Exception as e:  # network, auth, parse
            self.failures += 1
            self.notes.append(f"{h.get('id')}: LLM unavailable ({type(e).__name__}: {str(e)[:120]}); rule-based fallback")
            return base
        known = {f"{r['file']}:{r['line']}" for r in h.get("code_refs", [])}
        locs = data.get("suspect_locations", []) or []
        base["llm"] = {
            "root_cause_hypothesis": str(data.get("root_cause_hypothesis", ""))[:800],
            "explanation": str(data.get("explanation", ""))[:1500],
            "suspect_locations": [{"location": l, "matches_static_evidence": l in known} for l in locs[:6]],
            "fix_suggestions": [str(x)[:500] for x in (data.get("fix_suggestions") or [])[:5]],
            "confidence": data.get("confidence"),
            "status": "unverified-hypothesis",
        }
        base["mode"] = self.mode
        return base


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


class ClaudeCLIAnalyst(LLMAnalyst):
    """Uses the locally installed Claude Code CLI (`claude -p`) under the user's own login."""

    provider = "claude-cli"

    def _call(self, prompt: str) -> str:
        if not shutil.which("claude"):
            raise RuntimeError("claude CLI not found")
        argv = ["claude", "-p", "--output-format", "json", "--model", self.cfg.model, "--max-turns", "1",
                "--append-system-prompt", SYSTEM, "--disallowedTools", "Bash,Edit,Write,Read,WebFetch,WebSearch"]
        proc = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:200] or proc.stdout[:200])
        out = json.loads(proc.stdout)
        u = out.get("usage", {})
        self.input_tokens += int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
        self.output_tokens += int(u.get("output_tokens", 0))
        self.cost_usd += float(out.get("total_cost_usd") or 0.0)
        return out.get("result", "")


class AnthropicAnalyst(LLMAnalyst):
    provider = "anthropic"

    def _call(self, prompt: str) -> str:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        import anthropic  # optional dependency: pip install 'donotmix[ai]'

        client = anthropic.Anthropic()
        msg = client.messages.create(model=self.cfg.model, max_tokens=self.cfg.max_output_tokens, system=SYSTEM,
                                     messages=[{"role": "user", "content": prompt}])
        self.input_tokens += msg.usage.input_tokens
        self.output_tokens += msg.usage.output_tokens
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def make_analyst(cfg: AICfg, override: str | None = None):
    provider = (override or cfg.provider or "rule").lower()
    if provider in ("rule", "none", "off"):
        return RuleAnalyst()
    if provider == "claude-cli":
        return ClaudeCLIAnalyst(cfg)
    if provider == "anthropic":
        return AnthropicAnalyst(cfg)
    raise ValueError(f"unknown ai provider {provider!r} (rule|claude-cli|anthropic)")
