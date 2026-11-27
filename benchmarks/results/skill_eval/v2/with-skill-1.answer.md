# MixMart Staging flag-interaction check (run 20261127T025840Z, state hash 08015f56129a5db1)

## 1. Verdict: Staging is NOT safe to release
The live Staging state fails 3 tests (critical). There are 4 more latent hazards that fail only if other flags change. 25 configs were tested with 100% 2-way and 3-way coverage. Everything ran offline on a copy of the environment, and nothing in Flagsmith was changed.

## 2. Dangerous combinations (minimal core -> failing test)
| ID | Status | Core (flag values) | Failing test | Error | Confidence |
|---|---|---|---|---|---|
| HZ-1 | LIVE, critical | new_checkout=on, dynamic_pricing=on, discount_engine=on | test_member_checkout_total | discount 920 != 992 | sufficient |
| HZ-2 | LIVE, critical | new_checkout, dynamic_pricing, discount_engine, loyalty_program all on | test_loyalty_points | points 95 != 94 | sufficient |
| HZ-3 | LIVE, critical | recommendation=on (the live ai_search=on is also needed to trigger it) | test_recommendations | recommendation widget empty for a product with related items | held-in-sample |
| HZ-4 | latent, high | new_payment=on, express_shipping=off (with new_checkout=on) | test_loyalty_points, test_member_checkout_total | PaymentError: new gateway requires shipping_method | sufficient |
| HZ-5 | latent, high | guest_checkout=on, loyalty_program=on (with express_shipping=off) | test_guest_checkout_policy | AttributeError: 'NoneType' has no attribute 'points' | held-in-sample |
| HZ-6 | latent, high | guest_checkout=on, new_payment=on (with new_checkout=on, express_shipping=off) | test_guest_checkout_policy | PaymentError: new gateway requires shipping_method | held-in-sample |
| HZ-7 | latent, medium | recommendation=on, ai_search=on | test_recommendations | widget empty | held-in-sample |

Notes:
- HZ-2 is HZ-1 plus loyalty_program, so it has a different failing test.
- Only HZ-1, HZ-2 and HZ-4 are marked "sufficient". The rest are only held in the sample and are weaker evidence.
- The core lists only the flags that matter. "With ..." marks flags that were pinned during testing.

## 3. How to reproduce
Run each one from the repo directory with the Flagsmith credentials loaded:

    donotmix reproduce HZ-1 --run 20261127T025840Z

Use HZ-2 through HZ-7 in place of HZ-1 for the others. Each command applies only that hazard's core on an offline copy of the environment and runs the failing test(s).

## Proposed mitigation (NOT applied; needs human approval)
Each of the three proposals below was pre-verified by running the tests on the proposed state. Each resolves HZ-1, HZ-2 and HZ-3 with nothing still failing:
- P-1127025916-new_checko-recommenda: new_checkout=off and recommendation=off
- P-1127025916-dynamic_pr-recommenda: dynamic_pricing=off and recommendation=off
- P-1127025916-discount_e-recommenda: discount_engine=off and recommendation=off

Any option must turn recommendation off, because it is what breaks the widget. Turning new_checkout off also removes the precondition for HZ-4 and HZ-6. That makes it the broadest choice, though it does disable the whole new checkout. The latent hazards stay as constraints for later:
- Do not enable new_payment without express_shipping.
- Do not enable guest_checkout together with loyalty_program or new_payment.

To act on a proposal, a human reviews it and runs `donotmix approve <id>` in their own terminal. After that, `donotmix apply <id>` applies it and re-scans. I have not run approve or apply.

Code-level fixes, not verified:
- Make the discount and loyalty rounding consistent when dynamic_pricing and discount_engine are both on.
- Give new_payment a default shipping_method.
- Handle a missing loyalty account for guests.
- Fix the empty result in the recommendation widget.

Finite testing is not proof of safety. Details are in `.donotmix/runs/20261127T025840Z/report.md`.

## Summary
Staging is unsafe, with 3 live critical hazards and 4 latent ones. Turn off recommendation, plus one of new_checkout, dynamic_pricing or discount_engine (new_checkout is the broadest). The change must be approved by a human before anything is applied.
