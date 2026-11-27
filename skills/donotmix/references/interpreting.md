# Reading DoNotMix results

- `core`: the literals that must hold for the failure. Each was verified necessary by flipping it (the test then passes).
- `pinned`: literals forced by constraints given the core; not independent causes.
- `overrides_applied`: flags Flagsmith switches for that persona (segment/identity overrides), evaluated by the SDK.
- `trigger` + `trigger_claim`: smallest change from the reference state (live env or nearest passing state) that reproduces the failure. `1-minimal` = no single change can be dropped; `globally-minimal` = no smaller subset of the diff fails (exhaustively checked).
- `generality.verdict`:
  - `sufficient`: failed in every feasible completion of the other flags (exhaustive). Safe to state "whenever core holds, it fails".
  - `held-in-sample`: failed in all sampled completions; not exhaustive.
  - `context-dependent`: some completions pass; other flags matter. Do not overstate.
  - `masked` counts: the same test failed for another reason (another hazard), so the case is unresolved.
- `status: live` = the current environment already fails; `latent` = reachable by changing flags.
- Coverage ratios are over *feasible* t-tuples. 100% 3-way coverage does not guarantee 4-way faults are found.
- A clean scan means no failure was observed for the tested states, personas and tests. It is not a proof.
