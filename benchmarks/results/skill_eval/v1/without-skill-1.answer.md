# MixMart Staging flag-combination safety check

Tool: `donotmix scan` (risk strategy, t=3, 25 configs x 2 personas, run `20261127T024929Z`, real pytest suite `demo/mixmart/tests/test_storefront.py`). Nothing was changed in Flagsmith (read-only snapshot, scan, propose only).

## 1. Is the current Staging state safe?
**No.** Live state: new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on, recommendation=on, dark_mode=on; guest_checkout, express_shipping, new_payment, ai_search = off. Segment `beta_testers` overrides ai_search=on for beta users. This state has **3 live hazards** (all silent wrong results, no exception) and 4 latent ones (reachable with one or two flag flips).

## 2. Dangerous combinations (minimal sets) and failing tests
| ID | Status | Minimal flag values | Failing test | Failure |
|---|---|---|---|---|
| HZ-1 | LIVE | new_checkout=on + dynamic_pricing=on + discount_engine=on | test_member_checkout_total | AssertionError: discount 920 != 992 (both personas) |
| HZ-2 | LIVE | new_checkout + dynamic_pricing + discount_engine + loyalty_program all on | test_loyalty_points | AssertionError: points 95 != 94 (both personas) |
| HZ-3 | LIVE | recommendation=on (hits beta_tester because the segment forces ai_search=on) | test_recommendations | recommendation widget empty for product with related items |
| HZ-4 | latent | express_shipping=off + new_payment=on | test_loyalty_points, test_member_checkout_total | PaymentError: new gateway requires shipping_method |
| HZ-5 | latent | guest_checkout=on + loyalty_program=on | test_guest_checkout_policy | AttributeError: 'NoneType' has no attribute 'points' |
| HZ-6 | latent | guest_checkout=on + new_payment=on | test_guest_checkout_policy | PaymentError: new gateway requires shipping_method |
| HZ-7 | latent | recommendation=on + ai_search=on (env-wide, affects shopper too) | test_recommendations | same empty widget |

Notes: HZ-1/HZ-2 are verified necessary (flipping any one literal makes the test pass) and sufficient (fail in all feasible completions). HZ-1 is the pricing/discount interaction (surge price vs. discount computed on a different subtotal); HZ-2 is the same discrepancy leaking into loyalty points. HZ-3/HZ-7 root cause is ai_search returning lower-cased ids that the recommendation lookup misses; HZ-3 and HZ-7 generality is "held-in-sample" (sampled, not exhaustive). Project constraints in donotmix.yaml (express/new_payment need new_checkout, no express for guests) were respected; 512 of 1024 configs were feasible.

## 3. How to reproduce
From `/tmp/claude-501/skill-eval/without-skill-1` with `set -a; . ./.env; set +a` and the venv on PATH:
```
donotmix reproduce HZ-1 --run 20261127T024929Z   # exit 10 = still fails (same for HZ-2..HZ-7)
```
Each repro uses a saved environment document, e.g. HZ-1:
```
DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T024929Z/repro/HZ-1.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```
Full report: `.donotmix/runs/20261127T024929Z/report.md`. Diff from live state: HZ-1/2/3 none (live now); HZ-4 new_payment=on; HZ-5 guest_checkout=on; HZ-6 guest_checkout+new_payment=on; HZ-7 ai_search=on.

## Proposed mitigation for Staging (NOT applied; needs human approval)
`donotmix propose` produced pre-verified options, each resolving HZ-1, HZ-2 and HZ-3 (verified state passes 2/2 runs). All require turning `recommendation` off plus one of:
1. `discount_engine: on -> off` (first/recommended: smallest business scope, keeps new checkout under test)
2. `dynamic_pricing: on -> off`
3. `new_checkout: on -> off` (broadest impact)

Proposal ID for option 1: `P-1127025132-discount_e-recommenda`. A human must run `donotmix approve <id>` in an interactive terminal, then `donotmix apply`. Also recommended: add constraints for the latent hazards (`!(guest_checkout & loyalty_program)`, `!(guest_checkout & new_payment)`, `new_payment requires express/shipping_method`, `!(recommendation & ai_search)`), fix the code (checkout.py:47 area, loyalty.award_points with guest customer, new gateway needing shipping_method, ai_search lower-cased ids), and block release until fixed. Finite testing: a clean result would not prove safety.

## Summary
Staging is NOT safe to release: 3 live silent-wrong-result hazards (discount/pricing/checkout trio, the same plus loyalty points, and recommendation for beta testers via ai_search) and 4 latent crash/wrong-result hazards (new_payment without shipping, guest_checkout with loyalty or new_payment, recommendation with ai_search). Proposed fix pending approval: recommendation off plus one of discount_engine / dynamic_pricing / new_checkout off. No flags were changed.
