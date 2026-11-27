# DoNotMix demo video: script, shot list, captions (target 2:30)

Everything shown must be real output from this repository, captured from the local self-hosted Flagsmith and the real MixMart test suite. No mock screens.

| time | on screen (shot) | narration (English) |
|---|---|---|
| 0:00-0:12 | Black title cards, one line at a time: "Feature A works." "Feature B works." "Feature C works." | Feature A works. Feature B works. Feature C works. |
| 0:12-0:20 | Title card with hazard stripes: "But what happens when they work together?" then logo + "Safe alone. Dangerous together." | But what happens when they work together? Safe alone. Dangerous together. Meet DoNotMix. |
| 0:20-0:32 | `test_safe_alone.py`: 70 passed (each flag alone passes every scenario) | In our demo store, every feature flag passes every test on its own. Seventy out of seventy. |
| 0:32-0:50 | Flagsmith dashboard / `flagsmith flag list -e Staging`: Staging flags, beta_testers segment override | Flagsmith is the source of truth. DoNotMix reads the live Staging environment through the official Flagsmith CLI. It reads flags, segments and the full environment document, and it never writes. |
| 0:50-1:10 | Terminal: `donotmix scan -e Staging` progress, then the verdict LIVE_HAZARD and the hazard list | Ten flags means a thousand and twenty-four states. DoNotMix builds a covering array that hits every three-way combination in twenty-six configurations, and runs the real test suite on each, evaluated by the Flagsmith SDK, segment overrides included. |
| 1:10-1:35 | Dashboard → Hazard Report: HZ-1 core chips new_checkout + dynamic_pricing + discount_engine, "sufficient: fails in all completions", repro command; Reaction Matrix with hazard cells | The live Staging state is broken. Three flags together make checkout charge the wrong amount, with no error at all. Delta debugging shrinks the failure to exactly those three flags, and DoNotMix proves the condition is sufficient by re-testing every completion of the other seven. It also finds a bug that needs a flag to be OFF, and one that only beta users hit, through a Flagsmith segment override. |
| 1:35-1:55 | Dashboard → Benchmark tab: risk vs random bars, minimization exactness, MixMart 1,024 vs 25 | Measured, not claimed. On held-out benchmarks, at the same small budget, DoNotMix finds about ninety percent of planted faults where random testing finds about seventy. Minimization recovers the exact cause in over ninety percent of cases, with zero false positives. Where it lost, at the tiniest budget on our demo app, we say so. |
| 1:55-2:20 | `donotmix propose` → dashboard approval: type "Staging", approver name, Approve → Apply → re-scan shows HZ-1 gone; `donotmix audit` chain intact | Fixing it is a human decision. The agent proposes the smallest change, already verified against the tests. Only a human can approve it. Apply checks that nothing drifted, changes exactly that flag through the Flagsmith CLI, re-scans, and writes a tamper-evident audit log. |
| 2:20-2:40 | Agent Skill: SKILL.md, `skills-ref validate` → Valid; coding agent running the workflow; README Quick start `docker compose up` | And your coding agent can do all of this. The DoNotMix Agent Skill drives the whole Flagsmith job token-efficiently and stops at the human approval. One docker compose up gives you self-hosted Flagsmith, the demo and the dashboard. DoNotMix. Find the combinations that break your app, before your users do. |

## Capture checklist
1. `uv run pytest demo/mixmart/tests/test_safe_alone.py` (terminal)
2. `flagsmith flag list -e Staging -p MixMart` and Flagsmith UI at http://localhost:8000 (segment override)
3. `uv run donotmix scan -e Staging` (terminal, full run)
4. `uv run donotmix dashboard`: Overview, Reaction Matrix, Hazard Report (HZ-1), Benchmark, Audit
5. `uv run donotmix propose`, dashboard Approve + Apply, re-scan result, `uv run donotmix audit`
6. `skills-ref validate skills/donotmix`, SKILL.md, skill A/B table
7. Reset the demo afterwards: `bash scripts/seed_mixmart.sh`
