---
name: donotmix
description: Find dangerous feature-flag combinations in a Flagsmith project before they reach users. Snapshots an environment with the Flagsmith CLI, runs the project's real tests over a constrained combinatorial (2/3-way, risk-weighted) set of flag states, minimizes each failure to a reproducible core, and walks a human through an approved, drift-checked, re-verified staging change. Use when asked to check flag interactions, validate a flag rollout or release, debug a bug that only appears with certain flags on, or gate CI on flag safety.
license: Apache-2.0
compatibility: Requires the Flagsmith CLI v2 (`flagsmith`), Python 3.11+, the `donotmix` package, and network access to the Flagsmith API (Cloud or self-hosted).
metadata:
  author: donotmix
  version: "0.1.0"
  homepage: https://github.com/dorakingx/donotmix
allowed-tools: Bash(flagsmith:*) Bash(donotmix:*) Bash(bash:*) Bash(python3:*) Read
---

# DoNotMix: find flag combinations that break the app

Facts come from executed tests; you explain and coordinate. Never invent results.

## Hard rules
- Never run `flagsmith flag enable|disable|update|delete`, `feature delete` or any other write yourself. All changes go through `donotmix propose` -> human `donotmix approve` -> `donotmix apply`.
- Never run `donotmix approve` and never pass `--yes`/`--force` on someone's behalf. Ask the human to run approve in their own terminal (it requires a TTY) or click Approve in the dashboard.
- Never target a protected environment (default `Production`). Use Staging/Development.
- Do not paste whole reports, logs or environment documents into the conversation. Use `--brief`, `--json --jq`, and the scripts below.

## Workflow
1. **Preflight** (one call): `bash scripts/preflight.sh [ENV]`. It prints one JSON line with CLI version, auth, project/env resolution and config status. If `auth` is false, ask the user to run `flagsmith login` or export a host-scoped `FLAGSMITH_API_KEY*` (see references/flagsmith-cli.md). Stop on errors.
2. **Identify the target**: use the environment the user named; otherwise the `environment:` in donotmix.yaml. Confirm it is not protected.
3. **Flags and state** (compact): `bash scripts/flags.sh ENV` -> `[{f,on,seg}]`. Segment-scoped flags (`seg>0`) matter for personas.
4. **Configure once** if `donotmix.yaml` is missing: `donotmix init -p PROJECT -e ENV`, then fill `constraints`, `personas`, `runner.command`, `analysis.paths`. Use `python3 scripts/flag_refs.py FLAGS...` to see where flags are read (counts only). Spec: references/config.md. Validate with `donotmix doctor --json`.
5. **Dependency analysis**: `donotmix analyze --json --top 8` (risk prior only; it never decides pass/fail).
6. **Plan**: `donotmix scan -e ENV --dry-run --brief` shows configs and planned coverage. Default `--strategy risk --strength 3`; add `--budget N` for CI.
7. **Run**: `donotmix scan -e ENV --brief`. Exit codes: 0 clean, 10 latent hazards, 11 the live state fails, 3 config, 4 Flagsmith, 5 runner. Everything is evaluated offline on a copy of the environment document; nothing in Flagsmith changes.
8. **Minimized conditions** are in the brief (`core`, `generality`). Only call a condition "sufficient" when the brief says so; "held-in-sample" and "context-dependent" are weaker (references/interpreting.md).
9. **Explain and suggest fixes** for one hazard at a time: `donotmix report -f json | python3 scripts/hazard.py HZ-1` (core, failing tests, error, 3 code refs, repro command). Give the user the repro command and a concrete code fix; label your reasoning as unverified.
10. **Propose mitigation** for live hazards: `donotmix propose --json`. Each proposal is pre-verified by running the tests on the proposed state. Show the user the diff, environment, evidence and impact.
11. **Human approval**: ask the user to run `donotmix approve <id>` (or use the dashboard). Wait for their confirmation, then run `donotmix apply <id> --json`. Apply refuses unapproved, edited, drifted or protected changes and re-scans afterwards.
12. **Re-verify and audit**: report the re-scan status from apply's output and run `donotmix audit` (exit 0 = hash chain intact). If the change made things worse, offer `donotmix rollback <id>` (it needs approval too). Summarize: what failed, minimal condition, what was changed, by whom, re-verification result, remaining latent hazards, and that finite testing is not a proof.

## Output to the user
Short: verdict, hazards as `HZ-n core -> test (generality)`, repro command, proposed change, what needs their approval. Link `.donotmix/runs/<run>/report.md` for detail.
