# DoNotMix: submission texts (Toggle Wars: The Flagsmith Experimentation Hackathon)

> All numbers below come from `docs/BENCHMARKS.md` (generated from raw data). Re-check them against that file before pasting into the form.
> The rules pages list no form field limits. Each block is kept short, and character counts are noted so you can trim if the form imposes a limit.

---

## Project name
DoNotMix

## One-line pitch (≈110 chars)
Safe alone. Dangerous together. DoNotMix finds the feature-flag combinations that break your app, before your users do.

## Short description (≈300 chars)
DoNotMix snapshots a live Flagsmith environment with the official CLI. It runs your real tests over a small, mathematically chosen set of flag combinations and shrinks each failure to the minimal flags that reproduce it. Fixes reach Flagsmith only after a human approves them.

## Problem statement
Every feature flag ships with its own tests, and each one passes. Nobody tests the combinations. With N flags there are 2^N states (30 flags give over a billion). Teams flip flags independently, and segment overrides give different users different mixes. So the state that reaches production has often never run anywhere. NIST's studies of real failures show that most are triggered by interactions of only a few parameters, which is exactly the gap per-flag tests leave open. The worst cases throw no error: in our demo store, `new_checkout` + `dynamic_pricing` + `discount_engine` charge the wrong total, and nothing appears in the logs.

## Solution
DoNotMix is a verification engine, CLI, dashboard and Agent Skill.
1. **Snapshot** a Flagsmith environment read-only through the official CLI (`environment document`, `flag list`, `segment list`).
2. **Constrain** the space (`requires` / `excludes` / boolean rules). Only feasible states are tested, and contradictory rules are reported with a minimal conflicting subset.
3. **Analyze** the code: flags read in the same function, through calls, or guarding writes to the same field form a dependency hypergraph used as a risk prior.
4. **Plan** constrained covering arrays (2-way, 3-way, t-way), or a risk-weighted variable-strength cover that runs the most dangerous combinations first.
5. **Execute your real tests** for every (state, persona) against a rewritten copy of the environment document, with the Flagsmith SDK in offline mode, so segment overrides behave exactly as in Flagsmith.
6. **Minimize** each failure:
   - delta debugging finds the smallest trigger;
   - one-flip sensitivity finds the core;
   - re-testing all completions shows whether the core is *sufficient* or only *context-dependent*.
7. **Explain and propose** the smallest pre-verified mitigation, which a **human must approve**. Apply is drift-checked, changes exactly the approved flags, re-scans afterwards and writes a hash-chained audit log.

## Technical implementation
- Python engine. A custom CSP feasibility checker compiles constraints into nogoods via truth tables, so each check takes microseconds. Covering arrays are built with weighted greedy one-test-at-a-time construction (AETG-style; prioritized per Bryce & Colbourn), and risk hyperedges add variable strength. Minimization combines delta debugging (Zeller & Hildebrandt) with one-flip sensitivity analysis, counterexample-guided refinement and constraint-implied literal detection. Masking by other bugs is tracked as *unresolved*, never as a pass.
- Flagsmith CLI v2 is the only path to Flagsmith, always with `--json` or `--jq`. The Python SDK (`LocalFileHandler`) evaluates candidate states locally. An integration test proves that the server's `flagsmith evaluate` matches the offline evaluation for every persona.
- Typer CLI with CI exit codes (0 clean, 10 latent, 11 live hazard, 3/4/5/6 for tooling and approval errors). FastAPI with a no-build dashboard. Docker Compose one-command stack that bootstraps a self-hosted Flagsmith and seeds the demo through the CLI. GitHub Actions run unit tests and live integration tests against a containerized Flagsmith, and a reusable GitHub Action is included.
- AI: rule-based by default (deterministic and labelled). Optional Claude (`claude -p` or the Anthropic API) is budget-capped, receives only trimmed evidence, and its output is labelled *unverified hypothesis*. Pass or fail is never decided by an LLM.

## How we use Flagsmith
Flagsmith is the system under test, not a wrapper around it.
- **Official CLI v2** for every read (`auth status`, `environment get`, `flag list`, `environment document`, `segment list`, `evaluate`) and, only after human approval, writes (`flag enable` / `disable --yes`).
- **Environment document + Python SDK offline mode**: each candidate combination is evaluated by Flagsmith's own engine, segment overrides included.
- **Segments and identities**: personas reveal a bug that only the `beta_testers` segment sees, because a segment override turns `ai_search` on.
- **Self-hosted Flagsmith via Docker**: one `docker compose up` runs Flagsmith, bootstraps it, seeds MixMart, scans it and serves the dashboard.
- **Environments**: Production is protected; mitigations go to Staging after approval and are re-verified automatically.
Remove Flagsmith and DoNotMix has nothing to test and no way to change anything.

## What makes DoNotMix unique
Combinatorial testing and delta debugging are decades old, and we credit them. What is new is the closed loop around them:
- live Flagsmith state, segment overrides included;
- code-derived risk hypergraphs;
- your real test suite;
- minimal reproducible conditions with honest, separate claims ("1-minimal", "globally minimal", "sufficient in all N completions", or "context-dependent");
- a pre-verified mitigation that only a human can approve, applied with drift checks and an audit trail;
- an Agent Skill that gives a coding agent the whole workflow in a token-efficient way.
Most flag tools help you *change* flags safely. DoNotMix tells you which *combinations* are unsafe before anyone flips them.

## Benchmark results (measured; see docs/BENCHMARKS.md)
- **3-way covering arrays**: 18 / 23 / 28 / 34 tests achieve 100% feasible 3-way coverage for 10 / 15 / 20 / 30 flags. Exhaustive testing needs 768 / 24,576 / ~593k feasible states (30 flags: not runnable). They found 100% of ≤3-way faults at 10 to 20 flags and 99% at 30 flags.
- **Same small budget** (pairwise-suite size): risk-weighted DoNotMix found 92 / 93 / 90 / 92% of planted faults versus 67 / 72 / 76 / 76% for random testing (10 random seeds per case, 20 held-out cases per size), with earlier detection (NAPFD 0.64 to 0.68 vs 0.43 to 0.50). Ablations show that both the code-analysis prior and the change prior contribute.
- **Minimization**: 93 to 99% of hazards equal the planted fault exactly, with 0 false positives. Cores average 2.7 literals; the raw failing states differed from the live state in 8 to 27 flags.
- **Real app (MixMart, live Flagsmith)**: exhaustive testing takes 1,024 runs; DoNotMix needs 25 configurations for 100% 2- and 3-way coverage and matched all 4 planted bugs (3-way, 2-way, disabled-flag and segment-specific). Reported honestly: at an 8-test budget, risk ordering found 2/4 bugs on MixMart where pairwise found 4/4.
- **Agent Skill A/B**: see E5 in docs/BENCHMARKS.md.

## Agent Skill description (Track 04)
`skills/donotmix` (Agent Skills spec; passes `skills-ref validate`) takes a coding agent through a complete Flagsmith job built around the official CLI:
1. preflight auth;
2. identify the environment;
3. compact flag state via `flagsmith flag list --jq`;
4. configure constraints and personas;
5. dependency analysis;
6. plan;
7. scan;
8. read minimized hazards;
9. explain and propose a code fix;
10. propose a verified Staging mitigation;
11. **ask the human to approve** (the agent cannot);
12. apply, re-verify and audit.

Deterministic work lives in scripts. Every Flagsmith call is projected with `--jq`, scans return a ~1 KB `--brief`, and long material is split into `references/` that load only when needed. Hard safety rules: the agent never writes flags directly, never runs `approve`, and never touches Production.

## Challenges and limitations
- A failing test can hide another bug in the same test (masking). We track masking explicitly instead of counting it as a pass, but heavily masked failures can stay unminimized.
- Honest claims: finite testing is not proof. Minimal means 1-minimal or globally minimal *relative to the reference state*. "Sufficient" is only claimed after exhaustively re-testing all completions.
- Risk prioritization is a heuristic. It helped on average in held-out synthetic cases but lost to pairwise on MixMart at the tiniest budget.
- Flags are currently boolean (enabled state). Multivariate values are on the roadmap.
- The headless Claude Code CLI was not authenticated on our machine, so the Skill A/B was run with Claude Code sub-agents (same model, task, tools and workspace; harness-reported tokens). The method is documented in the benchmark file.

## Future roadmap
Multivariate and remote-config values · Flagsmith change requests as the approval path · persistent test workers · JS/TS runners · masking resolution by test splitting · contribution to the official Flagsmith skills catalog.

---

## Track 02: AI Agents and Feature Flags
**Criteria mapping.** *Use of Flagsmith CLI/API*: every Flagsmith interaction goes through the official CLI v2 with JSON output; the SDK evaluates states. *Time saved*: DoNotMix replaces 1,024 exhaustive runs with 25 configurations plus targeted minimization, and returns a minimal reproduction instead of a 10-flag diff. *Human approval*: the agent proposes, the human approves (TTY or dashboard, typed confirmation), apply is drift-checked and audited, and the demo video shows this step. *Clear output*: verdict, minimal core, generality claim, reproduction command and verified mitigation.

## Track 04: Flagsmith Agent Skills
**Criteria mapping.** *Completes the whole job*: preflight → ... → approved apply → re-verify → audit. *Token efficiency*: scripts, `--jq`, `--brief`, and progressive disclosure through references; measured A/B in E5. *Creative CLI use*: `environment document` becomes a test fixture, `evaluate` provides a parity oracle, `flag list --jq` gives compact state, and `flag enable/disable` is used only behind approval. *Safety around production*: Production is protected, there are no direct writes, approval is human-only, and changes are drift-checked.

## Track 03: Build on Open Source Flagsmith
**Criteria mapping.** *Self-hosted Flagsmith*: `docker compose up` runs self-hosted Flagsmith, bootstraps an admin, org and API key, seeds a demo project with the CLI, and brings up the DoNotMix dashboard. CI runs live integration tests against a containerized Flagsmith. *Developer tool / integration*: CLI, dashboard, GitHub Action and Agent Skill. *Ease of reuse*: `donotmix init`, documented runner contract, offline sample mode and Apache-2.0 license.
