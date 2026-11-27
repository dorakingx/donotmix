# DoNotMix architecture

```
               ┌──────────────── read-only ─────────────────┐
 Flagsmith ──► │ flagsmith.py  (official CLI v2, --json/--jq) │──► snapshot.json (flags, segments, env document, state hash)
               └──────────────────────────────────────────────┘
                                   │
 donotmix.yaml ─► config.py ─► space.py ── constraints → nogoods (truth tables) → CSP feasibility, MUS for conflicts
                                   │
 source code ─► analysis.py ── flag hypergraph (scope / call / resource / module / incident edges) ─► RiskModel
                                   │
                       generate.py ── exhaustive | random | t-way covering array | risk-weighted variable strength
                                   │
                       runner.py  ── (state, persona) → rewritten env document → YOUR tests (SDK offline mode)
                                   │                 → per-test failure atoms (test, exception kind, persona)
                       minimize.py ── ddmin trigger → one-flip core → generality over completions → refinement
                                   │
                       engine.py  ── hazards (live / latent), relations, coverage, reaction matrix, repro artifacts
                                   │
               ai.py (rule-based | claude-cli | anthropic, budgeted) ─► explanation (verified vs heuristic vs LLM)
                                   │
               report.py ─► report.json / report.md / report.html / --brief      dashboard.py ─► local UI
                                   │
 remediate.py ─► proposal (pre-verified) ─► approval.py: human approve (TTY/dashboard) ─► apply (drift check,
                 exact flags via `flagsmith flag enable|disable`, read-back) ─► re-scan ─► audit.jsonl (hash chain)
```

## Key design decisions

| Decision | Why |
|---|---|
| Test against a **rewritten copy of the environment document**, evaluated by the **Flagsmith SDK in offline mode** | No flag is ever toggled to run a test. Segment and identity overrides behave exactly as in Flagsmith. Parity with server-side `flagsmith evaluate` is checked by an integration test |
| **Failure atoms** = (persona, test, exception kind) | Lets the minimizer follow *one* failure. A different failure in the same test is *masking*, reported as unresolved and never as a pass |
| **Truth-table nogoods + tiny CSP** instead of Z3/SAT | Flag constraints touch few variables. Feasibility checks take microseconds, so the greedy covering array can call them millions of times. No heavy dependency |
| **Greedy one-test-at-a-time cover** with weights | Classic (AETG/density). Weights make the same algorithm a prioritized, variable-strength cover, which allows clean ablations (uniform weights = plain t-way) |
| Minimization claims are **separate**: trigger (1-minimal / globally minimal vs reference), core (sensitivity), generality (sufficient / held-in-sample / context-dependent) | Avoids over-claiming. "Sufficient" is only claimed after testing every feasible completion |
| **Rule-based explanations by default**, LLM opt-in | Deterministic CI, zero cost. When enabled, LLM output is labelled unverified and checked against static evidence |
| Writes only via `approval.apply` with an `ApprovalToken` | The adapter's write method refuses without a token bound to the approved flags and environment |
| TTY-only `approve` | Coding agents run non-interactive shells, so they cannot approve on a human's behalf |

## Exit codes
`0` clean · `10` latent hazards · `11` live state fails · `2` usage · `3` config / constraints · `4` Flagsmith · `5` runner · `6` approval.

## Extending
* **Another language**: any command works as a runner. Read `DONOTMIX_ENV_DOCUMENT` with your SDK's offline / local-evaluation mode, and emit JUnit XML to `{junit}` for per-test atoms.
* **Another flag source**: implement snapshot + `apply_config_to_document` equivalents in a new adapter.
* **Another strategy**: anything that returns a list of feasible configurations plugs into `engine.make_plan`.
