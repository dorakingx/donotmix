# MixMart Staging flag-safety check (run 20261127T025831Z, state hash 08015f56129a5db1)

## 1. Verdict: Staging is NOT safe to release
The current live Staging state fails 3 tests (exit code 11). 4 more combinations are latent: they fail if the flags are flipped, but not today. 25 configs were tested with full 2-way and 3-way coverage. Finite testing is not a proof of safety.

## 2. Dangerous combinations (minimal flag values, failing test)
| ID | Status | Minimal core | Failing test and error | Generality |
|---|---|---|---|---|
| HZ-1 | LIVE, critical | new_checkout=on, dynamic_pricing=on, discount_engine=on | test_member_checkout_total: discount 920 != 992 | sufficient |
| HZ-2 | LIVE, critical | HZ-1 core + loyalty_program=on | test_loyalty_points: points 95 != 94 | sufficient |
| HZ-3 | LIVE, critical | recommendation=on, with ai_search=on in the live state (ai_search is an override, not part of the core) | test_recommendations: widget empty for a product with related items | held-in-sample |
| HZ-4 | latent, high | express_shipping=off, new_payment=on (new_checkout=on pinned) | test_loyalty_points and test_member_checkout_total: PaymentError, new gateway requires shipping_method | sufficient |
| HZ-5 | latent, high | guest_checkout=on, loyalty_program=on (express_shipping=off pinned) | test_guest_checkout_policy: AttributeError, 'NoneType' has no attribute 'points' | held-in-sample |
| HZ-6 | latent, high | guest_checkout=on, new_payment=on (new_checkout=on, express_shipping=off pinned) | test_guest_checkout_policy: PaymentError, new gateway requires shipping_method | held-in-sample |
| HZ-7 | latent, medium | recommendation=on, ai_search=on | test_recommendations: widget empty | held-in-sample |

"Sufficient" means the core alone reproduces the failure. "Held-in-sample" means it held in the sampled configs only. Treat HZ-3 and HZ-5 to HZ-7 as likely but weaker claims.

## 3. Reproduce
Run from the repo directory with credentials loaded (`set -a; . ./.env; set +a`) and the venv on PATH:

    donotmix reproduce HZ-N --run 20261127T025831Z    # N = 1..7

Evaluation is offline on a copy of the environment document. Nothing in Flagsmith is touched. Detail is in `.donotmix/runs/20261127T025831Z/report.md`.

## Proposed mitigation (NOT applied, needs human approval)
Each proposal below was pre-verified by running the tests on the proposed state. Each resolves HZ-1, HZ-2 and HZ-3, with nothing still failing.
- Option A (smallest blast radius): discount_engine=off and recommendation=off (P-1127025902-discount_e-recommenda)
- Option B: dynamic_pricing=off and recommendation=off (P-1127025902-dynamic_pr-recommenda)
- Option C: new_checkout=off and recommendation=off (P-1127025902-new_checko-recommenda)

Process:
1. Run `donotmix propose --json` to list the proposals.
2. A human runs `donotmix approve <id>` in their own terminal. It needs a TTY.
3. Then run `donotmix apply <id> --json`, which re-scans afterwards.
4. Run `donotmix audit` to check the hash chain.

None of these has been approved or applied. The latent hazards stay open afterwards. Before enabling new_payment or guest_checkout, fix the shipping_method requirement (HZ-4, HZ-6) and the missing loyalty record for guests (HZ-5). Fix the recommendation widget when ai_search is on (HZ-7). The code fixes are my reasoning and are unverified.

## Summary
Staging fails now: 3 live critical hazards, a checkout discount and loyalty-points miscalculation plus an empty recommendations widget. There are 4 more latent hazards. I changed no flags. A human must approve one of the verified proposals, which I have not applied.
