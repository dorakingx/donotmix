# DoNotMix: WeMakeDevs submission form answers (as entered on 2026-11-27)

Numbers come from `docs/BENCHMARKS.md`, `benchmarks/results/skill_eval/v2/raw.jsonl` and `benchmarks/results/mixmart/summary.json`.

- **Project title:** DoNotMix — find the feature-flag combinations that break your app
- **Tracks:** Track 2 (AI Agents and Feature Flags), Track 3 (Build on Open Source Flagsmith), Track 4 (Flagsmith Agent Skills)
- **Demo video:** https://youtu.be/p8aRPsDP584 (unlisted, 2:55)
- **GitHub repository:** https://github.com/dorakingx/donotmix
- **Live demo:** (none)
- **Flagsmith parts used:** Feature flags, Segments, Flagsmith CLI, Flagsmith API (bootstrap of the self-hosted instance), Self-hosted Flagsmith
- **Track 4 token count:** 66764 (mean total tokens of the 3 with-skill runs: 66,667 / 66,894 / 66,732; without the skill: 79,946)

## What did you build, and what problem does it solve?
DoNotMix finds the feature-flag combinations that break your app, before your users do. Every flag ships with its own tests and each one passes, but nobody tests the combinations: 10 flags already give 1,024 states, and segment overrides give different users different mixes. The worst cases throw no error at all.

Who would use it: teams that ship behind Flagsmith flags (developers, QA, release managers) and the coding agents working for them, before a release or in CI.

How it works:
1. Snapshot a Flagsmith environment read-only through the official Flagsmith CLI (environment document, flag list, segment list).
2. Constrain the space with requires/excludes rules, so only feasible states are tested.
3. Analyze the code for flags that interact (read in the same function, through calls, or writing the same field) to build a risk prior.
4. Plan a constrained covering array. In the demo, 25 states cover every 2- and 3-way combination of 10 flags.
5. Run your real test suite on each state and persona. The Flagsmith SDK in offline mode evaluates each state, so segment overrides behave exactly as in Flagsmith.
6. Shrink each failure with delta debugging to the minimal flag combination, and check whether it is sufficient or only context-dependent.
7. Propose the smallest pre-verified flag change. Only a human can approve it. Apply is drift-checked, changes exactly the approved flags through the CLI, reads them back, re-scans and writes a hash-chained audit log.

It ships as a CLI with CI exit codes, a dashboard, a GitHub Action, a one-command Docker stack with self-hosted Flagsmith, and an Agent Skill. On the demo store it found all 4 planted bugs: a silently wrong checkout total caused by 3 flags together, a 2-flag crash, a bug that needs a flag switched OFF, and a bug that only the beta segment sees.

## Track 2: How does your agent work with Flagsmith?
CLI. Every Flagsmith call goes through the official Flagsmith CLI v2 (auth status, environment get, flag list, environment document, segment list, evaluate), always with --json or --jq. The Python SDK in offline mode evaluates candidate states locally; an integration test checks that the server's `flagsmith evaluate` agrees with the offline evaluation for every persona.

The job: before a release, find which flag combinations break the app, and return a minimal reproduction plus a verified mitigation. Time saved: on the demo app, exhaustive testing needs 1,024 states; DoNotMix needs 25 for full 2- and 3-way coverage plus targeted minimization (the scan took about 16 s), and it returns a 2- or 3-flag cause instead of a 10-flag diff.

Human approval: the agent can only propose. A human approves in an interactive terminal (donotmix approve) or in the dashboard by typing the environment name; without that, apply exits with code 6. Apply then checks the approval hash and that the live flags have not drifted, changes exactly the approved flags with flagsmith flag enable/disable, reads them back, re-scans and writes a hash-chained audit log. Production is protected in config. The video shows a real approved apply on Staging: the live hazard disappears (exit code 11 -> 10).

## Track 3: How did you build on self-hosted Flagsmith?
Docker Compose. One `docker compose up` starts self-hosted Flagsmith with Postgres, bootstraps an admin user, organisation and master API key through the Flagsmith REST API, seeds the MixMart demo project (10 flags, plus a beta_testers segment with an override) using only the Flagsmith CLI, runs a first scan and serves the DoNotMix dashboard. GitHub Actions run live integration tests against a containerized Flagsmith on every push.

What we built around it: a developer tool and integration. It comes as a CLI, a dashboard, a reusable GitHub Action (action.yml) and an Agent Skill.

Reuse: install with pip from the repo and run `donotmix init` to write a config. Point it at your environment and give it a test command: any runner that reads the flag state from environment variables or writes a JUnit report works, and pytest gets a fast warm-worker runner. An offline sample snapshot lets you try it without a Flagsmith server. Apache-2.0.

## Track 4: What does your skill do?
skills/donotmix (Agent Skills spec; passes skills-ref validate) takes a coding agent through a full pre-release flag check:
1. preflight auth (flagsmith auth status);
2. identify the environment (flagsmith environment get);
3. compact flag state (flagsmith flag list --jq);
4. configure constraints and personas;
5. dependency analysis, plan and scan;
6. read the minimized hazards and explain them;
7. propose a pre-verified Staging mitigation;
8. ask the human to approve;
9. apply, re-verify and audit.

A one-call fast path (scripts/release_check.sh) returns a ~1 KB brief, and longer material sits in references/ and loads only when needed.

Safety: the skill's hard rules forbid direct flag writes, running approve, or touching Production. DoNotMix itself refuses to apply without a human approval (exit code 6) and protects Production in config.

Tested with Claude Code sub-agents on Claude Sonnet: 3 runs per arm, with the same task, tools, commit and Flagsmith state. With the skill, the runs averaged 66,764 total tokens (66,667 / 66,894 / 66,732), 3 tool calls and 61 s. Without the skill they averaged 79,946 tokens, 5.7 tool calls and 90 s. That is 16.5% fewer tokens and 47% fewer tool calls. Both arms completed every run with no unapproved flag change. Tokens are the harness-reported totals (input + output, including cached context). Our first version (v1) gave no saving (+2.6%), and we report that too.

## Feedback: what did you like about Flagsmith, and what would you improve?
What we liked: the CLI v2 is excellent for agents. --json and --jq on every command keep outputs tiny. `environment document` plus the SDK's offline mode let us evaluate thousands of flag states locally with Flagsmith's own engine, segment overrides included, without touching the server. `flagsmith evaluate` made a perfect parity check. Self-hosting with Docker was quick.

What got in the way: for a self-hosted host, the CLI ignores FLAGSMITH_API_KEY and needs a host-scoped variable (FLAGSMITH_API_KEY_localhost_8000), which took a while to find. Write commands need --yes when there is no TTY. That is good for safety, but it surprised us. `segment create --rules` expects a JSON object, which was not obvious. Bootstrapping a fresh self-hosted instance (createsuperuser needs --first_name and --sign_up_type) took some trial and error.

What we'd want next: change requests and scheduled changes from the CLI, so an agent can open an approval request instead of writing; a CLI command to evaluate a hypothetical flag state locally; and an official Agent Skills catalog to contribute to.
