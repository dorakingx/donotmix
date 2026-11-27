# MixMart Staging flag-safety check (DoNotMix run 20261127T024625Z, state hash 08015f56129a5db1)

Nothing was changed in Flagsmith. All evaluation was offline on a copy of the Staging environment document, running `demo/mixmart/tests/test_storefront.py` over 25 risk-weighted configs x 2 personas (pairwise and 3-way coverage 100%).

## 1. Is current Staging safe?
**No (exit 11, LIVE_HAZARD).** The live Staging state fails 3 critical hazards. Current state: new_checkout, dynamic_pricing, discount_engine, loyalty_program, recommendation, dark_mode ON; guest_checkout, express_shipping, new_payment OFF; ai_search OFF by default but ON for the `beta_testers` segment.

## 2. Dangerous combinations (minimal core -> failing test)
Live now:
- **HZ-1** (critical, sufficient): new_checkout=on + dynamic_pricing=on + discount_engine=on -> `test_member_checkout_total` (discount 920 != 992; the discount is computed on a different price than the surge-priced total). Shopper and beta_tester.
- **HZ-2** (critical, sufficient): HZ-1 core + loyalty_program=on -> `test_loyalty_points` (points 95 != 94, same price mismatch). Shopper and beta_tester.
- **HZ-3** (critical, held-in-sample): recommendation=on -> `test_recommendations` (widget empty for a product with related items). Affects the beta_tester persona only, because the segment turns ai_search on for them. Effectively recommendation + ai_search, since search.py lowercases SKUs and breaks `also_bought`. Same mechanism as HZ-7.

Latent (not live in Staging today, but one flag flip away):
- **HZ-4** (high, sufficient): new_payment=on while express_shipping=off (new_checkout is implied by constraints) -> `test_loyalty_points` and `test_member_checkout_total` raise PaymentError "new gateway requires shipping_method". All 128 completions fail.
- **HZ-5** (high, held-in-sample): guest_checkout=on + loyalty_program=on -> `test_guest_checkout_policy` AttributeError ('NoneType' has no 'points'; guests have no customer account).
- **HZ-6** (high, held-in-sample): guest_checkout=on + new_payment=on (new_checkout implied) -> `test_guest_checkout_policy` PaymentError (missing shipping_method).
- **HZ-7** (medium, held-in-sample): recommendation=on + ai_search=on -> `test_recommendations`, for the shopper persona. This is what happens if ai_search is enabled environment-wide.

"held-in-sample" means the core held in all sampled completions, not an exhaustive proof. Finite testing is not proof of safety.

## 3. Reproduction
From the repo root, after `set -a; . ./.env; set +a` and with the donotmix venv on PATH:
- `donotmix reproduce HZ-N --run 20261127T024625Z` for N = 1..7 (HZ-1 to HZ-3 are live, HZ-4 to HZ-7 latent).
- Or directly, e.g. HZ-1:
  `DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T024625Z/repro/HZ-1.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly`
  (use `shopper-1` / `{"beta": false}` for the shopper persona; HZ-7 uses the shopper).
- Full detail: `.donotmix/runs/20261127T024625Z/report.md`.

## Proposed mitigation (awaiting human approval, NOT applied)
`donotmix propose` produced three pre-verified proposals (each ran the tests on the proposed state and passes; all three resolve HZ-1, HZ-2 and HZ-3):
1. `P-1127024820-discount_e-recommenda`: discount_engine true->false, recommendation true->false (keeps the new checkout and surge pricing under test).
2. `P-1127024820-dynamic_pr-recommenda`: dynamic_pricing true->false, recommendation true->false.
3. `P-1127024820-new_checko-recommenda`: new_checkout true->false, recommendation true->false. Least attractive before a release, since it disables the checkout being shipped.

Suggested: option 1 or 2, as a temporary Staging gate. The real fixes are in code: compute the discount on the same surge-priced amounts the total uses (`checkout.py`, `discounts.py`, `pricing.py`); lowercase-safe SKU handling in `search.py`/`recommend.py`; make `new_payment` default a shipping_method (or add a constraint requiring express_shipping); and guard `award_points` for guests (`customer is None`). Also consider adding `new_payment` and `guest_checkout` interactions to constraints. These code suggestions are my reasoning and are unverified.

To proceed, a human should review and run, in their own terminal: `donotmix approve <proposal-id>`, then `donotmix apply <proposal-id> --json` (it re-scans afterwards), and `donotmix audit`. I have not approved or applied anything.

## Summary
Staging is NOT safe to release: 3 live critical hazards (new_checkout+dynamic_pricing+discount_engine; the same plus loyalty_program; recommendation for beta testers via the ai_search segment override) and 4 latent ones (new_payment without express_shipping; guest_checkout with loyalty_program or new_payment; recommendation+ai_search). Three verified mitigation proposals await your approval; no flag has been changed.
