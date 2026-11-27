# donotmix.yaml

```yaml
project: MixMart                 # Flagsmith project (name or id)
environment: Staging             # default environment to snapshot
api_url: http://localhost:8000   # omit for Flagsmith Cloud
protected_environments: [Production]
flags: [a, b, c]                 # optional subset; default = all flags
ignore_flags: [dark_mode]
constraints:                     # only feasible states are tested; infeasible ones are explained
  - {id: x, requires: [express_shipping, new_checkout], reason: "..."}   # A needs B
  - {excludes: [guest_checkout, express_shipping]}                        # not together
  - {fixed: {ai_search: false}, reason: "segment-only rollout"}           # pinned value
  - {rule: "(a & b) -> !c"}                                               # ! & | -> <-> ( )
derive_segment_constraints: false   # true: flags on only via segment overrides stay off env-wide
personas:                        # identities evaluated by the Flagsmith SDK (segments apply)
  - {id: shopper, identifier: shopper-1, traits: {beta: false}}
  - {id: beta, identifier: beta-1, traits: {beta: true}}
runner:
  type: command                  # or python (module:function) for in-process suites
  command: "pytest -q -p no:cacheprovider --junitxml={junit}"
  jobs: 8
analysis: {paths: [src]}         # source scanned for flag co-occurrence (risk prior)
strategy: {name: risk, strength: 3, seed: 0, budget: null}   # exhaustive|random|pairwise|tway|risk
ai: {provider: rule}             # rule (default, deterministic) | claude-cli | anthropic; budget-capped
```

Runner contract: for each candidate state DoNotMix runs the command with
`DONOTMIX_ENV_DOCUMENT` (rewritten copy of the environment document), `DONOTMIX_FLAGS` (JSON),
`DONOTMIX_IDENTITY`, `DONOTMIX_TRAITS`. The app's Flagsmith SDK must read flags in offline /
local-evaluation mode from that document, e.g. Python:
`Flagsmith(offline_mode=True, offline_handler=LocalFileHandler(os.environ["DONOTMIX_ENV_DOCUMENT"]))`.
`{junit}` gives per-test failures; without it the exit status is used.
