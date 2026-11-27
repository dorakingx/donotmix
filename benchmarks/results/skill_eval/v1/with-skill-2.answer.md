# MixMart Staging flag-safety check

Run `20261127T025305Z` (report: `/tmp/claude-501/skill-eval/with-skill-2/.donotmix/runs/20261127T025305Z/report.md`). It used DoNotMix with risk-weighted 3-way coverage: 25 configurations x 2 personas (shopper, beta_tester), and 100% of feasible 2-way and 3-way tuples were covered. It ran the real tests in `demo/mixmart/tests/test_storefront.py` against offline copies of the Staging environment document. Nothing in Flagsmith was changed.

## 1. Is Staging safe?
**No.** The current live Staging state fails (exit code 11), with 3 live hazards and 4 latent ones that are reachable by flipping flags.

Current state:
- On: new_checkout, dynamic_pricing, discount_engine, loyalty_program, recommendation, dark_mode.
- Off: guest_checkout, express_shipping, new_payment.
- ai_search is off in the environment, but a segment override (`beta_testers`) turns it on for beta testers.

## 2. Dangerous combinations
Each core below is the minimal set of flag values needed to reproduce the failure. Flipping any single one of them makes the test pass.

| ID | Status | Core (minimal) | Failing test and error | Generality |
|---|---|---|---|---|
| HZ-1 | LIVE, critical | new_checkout=on, dynamic_pricing=on, discount_engine=on | `test_member_checkout_total`: discount 920 != 992 (discount computed on a different price than the one charged) | sufficient (both personas) |
| HZ-2 | LIVE, critical | HZ-1 core plus loyalty_program=on | `test_loyalty_points`: points 95 != 94 | sufficient (both personas) |
| HZ-3 | LIVE, critical | recommendation=on (beta_tester only; the beta segment turns ai_search on) | `test_recommendations`: recommendation widget is empty for a product with related items | held-in-sample, not exhaustive |
| HZ-4 | latent, high | new_payment=on, express_shipping=off (new_checkout forced on by the constraints) | `test_member_checkout_total` and `test_loyalty_points`: PaymentError "new gateway requires shipping_method" | sufficient |
| HZ-5 | latent, high | guest_checkout=on, loyalty_program=on | `test_guest_checkout_policy`: AttributeError, 'NoneType' has no attribute 'points' (guest has no account to award points to) | held-in-sample |
| HZ-6 | latent, high | guest_checkout=on, new_payment=on | `test_guest_checkout_policy`: PaymentError "new gateway requires shipping_method" | held-in-sample |
| HZ-7 | latent, medium | recommendation=on, ai_search=on (shopper) | `test_recommendations`: recommendation widget is empty | held-in-sample |

Notes:
- HZ-1 and HZ-2 are the same root cause. Together they make up the 3-way interaction between dynamic pricing, discount engine and new checkout.
- HZ-3 and HZ-7 are the same bug: `search.py` lowercases SKUs when ai_search is on, and recommendation looks them up case-sensitively. HZ-3 is live for beta testers only because of the segment override. Shoppers hit it only if ai_search is turned on environment-wide.
- HZ-4 and HZ-6 share one cause: `shipping.select` returns no method unless express_shipping is on, but the new gateway needs one. Turning on new_payment while express_shipping is off breaks checkout.
- A clean scan would not be proof of safety, and this finite test set cannot prove the absence of other hazards.

## 3. How to reproduce
Run from `/tmp/claude-501/skill-eval/with-skill-2` with the `.env` credentials loaded and the donotmix venv on PATH:

```
donotmix reproduce HZ-1 --run 20261127T025305Z   # likewise HZ-2 ... HZ-7
```

Each command replays the minimal environment document with the matching persona. For example, HZ-1 runs:
`DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T025305Z/repro/HZ-1.environment.json DONOTMIX_IDENTITY=shopper-1 DONOTMIX_TRAITS='{"beta": false}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly`

HZ-3 uses `DONOTMIX_IDENTITY=beta-1` with `DONOTMIX_TRAITS='{"beta": true}'`.

## Proposed mitigation for Staging (nothing has been applied)
`donotmix propose` created three alternative proposals, all awaiting human approval. Each was pre-verified by running the tests on the proposed state (2 of 2 passes, nothing still failing) and resolves HZ-1, HZ-2 and HZ-3:

1. `P-1127025539-discount_e-recommenda`: discount_engine true -> false, recommendation true -> false.
2. `P-1127025539-dynamic_pr-recommenda`: dynamic_pricing true -> false, recommendation true -> false.
3. `P-1127025539-new_checko-recommenda`: new_checkout true -> false, recommendation true -> false.

All three also disable `recommendation`, because HZ-3 is on its own in the live state and cannot be separated from that flag. Option 1 or 2 is the least disruptive, since it keeps new_checkout on, which the express_shipping and new_payment constraints depend on. Whichever is chosen, a human has to decide which of the HZ-1/HZ-2 flags to give up.

Not covered by the proposals, as a guard for any later rollout:
- Keep new_payment off, or turn on express_shipping with it. Do not turn on guest_checkout together with new_payment or loyalty_program.
- Fixing the code removes the hazards instead of disabling features:
  - Compute the discount on the surge-adjusted price consistently in `checkout`/`discounts`/`loyalty`.
  - Make `shipping.select` return a method when express_shipping is off.
  - Guard `award_points` against a guest (`customer is None`).
  - Normalise SKU case in `recommend.also_bought`.
  These code fixes are my reading of the sources and have not been tested.

**To approve (human only):** run `donotmix approve <proposal-id>` in your own terminal, then `donotmix apply <proposal-id> --json`. Apply refuses unapproved, drifted or protected changes, re-scans afterwards, and `donotmix audit` checks the hash chain.

## Summary
Staging is NOT safe. Three live critical hazards:
- HZ-1: new_checkout + dynamic_pricing + discount_engine gives a wrong discount.
- HZ-2: the same three flags plus loyalty_program give wrong loyalty points.
- HZ-3: recommendation, for beta testers, shows an empty widget.

Four latent hazards are one flag-flip away: HZ-4, HZ-5, HZ-6 and HZ-7, covering new_payment without express_shipping, guest_checkout with loyalty_program or new_payment, and ai_search with recommendation. Three pre-verified proposals await human approval; no flag was changed.
