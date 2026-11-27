# MixMart Staging flag-safety check (run 20261127T024934Z, state hash 08015f56129a5db1)

## 1. Verdict: Staging is NOT safe
`donotmix scan -e Staging` exited 11 (the live state fails). 25 configurations x 2 personas (risk strategy, 3-way coverage 100%). Nothing in Flagsmith was changed.

Live Staging state: ON = new_checkout, dynamic_pricing, discount_engine, loyalty_program, recommendation, dark_mode. OFF = guest_checkout, express_shipping, new_payment. ai_search is OFF globally but ON via a segment (the beta_tester persona gets it).

## 2. Dangerous combinations (minimal cores)
Live (fail right now):
| ID | Minimal flag values | Failing test | Failure | Generality |
|---|---|---|---|---|
| HZ-1 (critical) | new_checkout=on, dynamic_pricing=on, discount_engine=on | test_member_checkout_total | discount 920 != 992 (wrong total) | sufficient (both personas) |
| HZ-2 (critical) | HZ-1 core + loyalty_program=on | test_loyalty_points | points 95 != 94 | sufficient (both personas) |
| HZ-3 (critical) | recommendation=on (with ai_search on via the beta segment) | test_recommendations | recommendation widget empty | held-in-sample, beta_tester only. Likely recommendation + ai_search (see HZ-7) |

Latent (not live now, but reachable by flipping a flag):
| ID | Minimal flag values | Failing test | Failure | Generality |
|---|---|---|---|---|
| HZ-4 (high) | new_payment=on with express_shipping=off (new_checkout on) | test_loyalty_points, test_member_checkout_total | PaymentError: new gateway requires shipping_method | sufficient |
| HZ-5 (high) | guest_checkout=on, loyalty_program=on | test_guest_checkout_policy | AttributeError: 'NoneType' has no attribute 'points' (guest has no account) | held-in-sample |
| HZ-6 (high) | guest_checkout=on, new_payment=on (new_checkout on, express off) | test_guest_checkout_policy | PaymentError: new gateway requires shipping_method | held-in-sample |
| HZ-7 (medium) | recommendation=on, ai_search=on | test_recommendations | recommendation widget empty (shopper) | held-in-sample |

Only HZ-1, HZ-2, HZ-4 are "sufficient"; the others are weaker evidence from the sample. Suggested code fixes are my reasoning and are unverified: ai_search lowercases SKUs and recommendation looks them up case-sensitively (search.py:11 vs recommend.py); award_points dereferences customer for guests (loyalty.py:10); new payment requires shipping_method when express shipping is off; discount/dynamic pricing disagree on the discount base.

## 3. Reproduce
From the repo dir, with `.env` loaded and the donotmix venv on PATH:
`donotmix reproduce HZ-n --run 20261127T024934Z` for n = 1..7 (HZ-1 expands to `DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T024934Z/repro/HZ-1.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly`). Full detail: `.donotmix/runs/20261127T024934Z/report.md`. Finite testing is not a proof of absence of other hazards.

## Mitigation proposals (pre-verified, awaiting human approval, NOT applied)
Each fixes HZ-1, HZ-2, HZ-3 (2/2 verification runs pass, no remaining failures):
1. `P-1127025135-discount_e-recommenda`: discount_engine -> off, recommendation -> off
2. `P-1127025135-dynamic_pr-recommenda`: dynamic_pricing -> off, recommendation -> off
3. `P-1127025135-new_checko-recommenda`: new_checkout -> off, recommendation -> off

Option 3 is likely the worst: new_checkout=off would also disable the new checkout work under test. Option 1 or 2 is the narrowest. To approve, run in your own terminal: `donotmix approve <id>`, then tell me and I will run `donotmix apply <id> --json` and `donotmix audit`. Latent HZ-4 to HZ-7 should be blocked (e.g. via constraints or code fixes) before turning on new_payment, guest_checkout or ai_search globally.

## Summary
Staging is unsafe: 3 live critical hazards (new_checkout+dynamic_pricing+discount_engine wrong discount; plus loyalty_program wrong points; recommendation empty under ai_search) and 4 latent ones. No flags were changed; awaiting your approval of one of the proposals.
