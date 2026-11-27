# MixMart Staging flag-safety check (run 20261127T025849Z)

## 1. Verdict: Staging is NOT safe to release
The current Staging state fails 3 tests (exit 11). 4 more combinations are latent, so they are not live now but one flag flip would trigger them. The scan tested 25 configs with 100% 2-way and 3-way coverage. Nothing was changed in Flagsmith.

## 2. Dangerous combinations (minimal core, failing test)
| ID | Status | Minimal flag values | Failing test and error | Generality |
|---|---|---|---|---|
| HZ-1 | LIVE, critical | new_checkout=on, dynamic_pricing=on, discount_engine=on | test_member_checkout_total: discount 920 != 992 | sufficient |
| HZ-2 | LIVE, critical | the HZ-1 flags plus loyalty_program=on | test_loyalty_points: points 95 != 94 | sufficient |
| HZ-3 | LIVE, critical | recommendation=on. The beta_tester segment override has ai_search=on. | test_recommendations: widget is empty for a product with related items | held-in-sample |
| HZ-4 | latent, high | new_payment=on, express_shipping=off (new_checkout=on pinned) | test_loyalty_points and test_member_checkout_total: PaymentError, new gateway requires shipping_method | sufficient |
| HZ-5 | latent, high | guest_checkout=on, loyalty_program=on (express_shipping=off pinned) | test_guest_checkout_policy: AttributeError, 'NoneType' has no attribute 'points' | held-in-sample |
| HZ-6 | latent, high | guest_checkout=on, new_payment=on (new_checkout=on and express_shipping=off pinned) | test_guest_checkout_policy: PaymentError, new gateway requires shipping_method | held-in-sample |
| HZ-7 | latent, medium | recommendation=on, ai_search=on | test_recommendations: empty widget | held-in-sample |

Only call HZ-1, HZ-2 and HZ-4 sufficient. The held-in-sample ones are weaker evidence, and the cause of HZ-3 and HZ-7 may be context-dependent. HZ-3 and HZ-7 look like the same bug: recommendation together with ai_search.

## 3. How to reproduce
Run `set -a; . ./.env; set +a` first, then the command for the hazard:
`donotmix reproduce HZ-N --run 20261127T025849Z` (N = 1 to 7).
Detail: `.donotmix/runs/20261127T025849Z/report.md`.

## Proposed mitigation (needs human approval, not applied)
Each of these three proposals was pre-verified by re-running the tests on the proposed state. Each resolves HZ-1, HZ-2 and HZ-3 with nothing still failing:
- P-1127025922-new_checko-recommenda: new_checkout=off and recommendation=off. This is the proposal to start with. Turning off new_checkout also removes the preconditions of the latent HZ-4 and HZ-6, whose cores are pinned to new_checkout=on.
- P-1127025922-dynamic_pr-recommenda: dynamic_pricing=off and recommendation=off.
- P-1127025922-discount_e-recommenda: discount_engine=off and recommendation=off.

To approve, a human runs `donotmix approve <id>` in their own terminal. After that, `donotmix apply <id>` re-scans and `donotmix audit` checks the audit chain. Latent hazards HZ-5 and HZ-7 remain in every option. HZ-4 and HZ-6 remain in the dynamic_pricing and discount_engine proposals, so avoid enabling new_payment, guest_checkout or ai_search until they are fixed. The underlying code bugs should be fixed too: rounding in the discount and points calculation, new_payment requiring shipping_method, guest checkout with a null loyalty account, and recommendation under ai_search. Those code-fix suggestions are unverified. Finite testing is not a proof of safety.

## Summary
Staging is unsafe because of 3 live hazards (the new_checkout, dynamic_pricing and discount_engine pricing and loyalty bugs, plus recommendation), and there are 4 latent ones. Recommended change, pending human approval: new_checkout=off and recommendation=off. No flags were modified.
