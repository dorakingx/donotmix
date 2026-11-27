# MixMart Staging pre-release flag safety check

Tool: `donotmix scan` (risk strategy, t=3, seed 0). It ran 25 configs x 2 personas (shopper, beta_tester) against the real storefront tests (`demo/mixmart/tests/test_storefront.py`). Pairwise and 3-way coverage were both 100%. Run id: `20261127T024616Z`. The run was read-only, and no flag in Flagsmith was changed.

## 1. Is current Staging safe?
**No (LIVE_HAZARD).** The live Staging state fails 3 hazards.

Live Staging flags: new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on, recommendation=on, dark_mode=on. guest_checkout, express_shipping, new_payment and ai_search are off.

The `beta_testers` segment overrides ai_search=on. This is why beta testers show extra failures.

## 2. Dangerous combinations (minimal flag values)
| ID | Status | Minimal core | Failing test and error |
|---|---|---|---|
| HZ-1 | LIVE | new_checkout=on + dynamic_pricing=on + discount_engine=on | `test_member_checkout_total`: AssertionError `discount 920 != 992` (silent wrong discount) |
| HZ-2 | LIVE | HZ-1 core + loyalty_program=on | `test_loyalty_points`: AssertionError `points 95 != 94` (silent wrong points) |
| HZ-3 | LIVE | recommendation=on (single flag) | `test_recommendations`: `recommendation widget is empty for a product with related items` |
| HZ-4 | latent | express_shipping=off + new_payment=on (needs new_checkout=on) | `test_loyalty_points`: PaymentError `new gateway requires shipping_method` |
| HZ-5 | latent | guest_checkout=on + loyalty_program=on | `test_guest_checkout_policy`: AttributeError `'NoneType' object has no attribute 'points'` (loyalty awards points to a guest) |
| HZ-6 | latent | guest_checkout=on + new_payment=on | `test_guest_checkout_policy`: PaymentError `new gateway requires shipping_method` |
| HZ-7 | latent | recommendation=on + ai_search=on | `test_recommendations`: same empty-widget failure |

Notes:
- HZ-1 and HZ-2 fail for both personas. Every literal in each core is necessary. Exhaustive completions confirm HZ-1 fails in 64/96 and HZ-2 in 32/48. The rest are masked by other flags.
- HZ-3 (and HZ-7) are likely the same bug. The failure happens only through the ai_search path, which is on for the beta_testers segment. For shopper-1, HZ-3 does not reproduce on its own, and the shopper path shows up as HZ-7. These were checked on a sample, not exhaustively. The ai_search lowercases SKUs and the recommendation code looks up the normalised ids, so this is a mismatch. This is my reading of the code, not a verified root cause.
- The 3 latent hazards (HZ-4/5/6) are not live now. They would become live if someone turns on new_payment or guest_checkout. HZ-4 and HZ-6 both come from the new payment gateway needing a shipping_method. HZ-5 comes from loyalty points being awarded to a guest (no customer).
- Config constraints in `donotmix.yaml` do not forbid any of these combinations. The only constraints are express/new_payment requires new_checkout, and no express for guests.
- Finite testing: a clean result would not prove safety. Only the executed configs, tests and personas are covered.

## 3. How to reproduce
```bash
cd /tmp/claude-501/skill-eval/without-skill-0
set -a; . ./.env; set +a
export PATH=$HOME/Developer/App/donotmix/.venv/bin:$PATH
donotmix scan                                      # full scan, report in .donotmix/runs/<run>/report.md
donotmix reproduce HZ-<n> --run 20261127T024616Z   # n = 1..7
```
I re-ran `reproduce` for all 7 hazards. All 7 still FAIL with the same errors as above. Each report entry also gives the raw pytest command, using the saved environment document `.donotmix/runs/20261127T024616Z/repro/HZ-<n>.environment.json` and `DONOTMIX_IDENTITY` / `DONOTMIX_TRAITS`.

## Mitigation proposal for Staging (NOT applied, human approval required)
`donotmix propose` produced 3 equal-sized options. Each was pre-verified: Staging passes 2/2 test runs and the options resolve HZ-1, HZ-2 and HZ-3. The options are:
1. `discount_engine: on -> off` and `recommendation: on -> off` (`P-1127024823-discount_e-recommenda`)
2. `dynamic_pricing: on -> off` and `recommendation: on -> off` (`P-1127024823-dynamic_pr-recommenda`)
3. `new_checkout: on -> off` and `recommendation: on -> off` (`P-1127024823-new_checko-recommenda`)

Recommendation: option 1 or 2. They are the least invasive, because turning off new_checkout would also affect express_shipping and new_payment, which depend on it. The choice between discount_engine and dynamic_pricing is a product decision. Each of the three options switches off 2 flags in Staging only. Production is a protected environment and is not touched. Turning off recommendation is a workaround for HZ-3. The real fixes are code fixes: the discount and loyalty calculation under dynamic pricing in `checkout.py`, `discounts.py` and `loyalty.py`, and the SKU normalisation in `recommend.py`/`search.py`.

To go ahead, a human runs `donotmix approve <id>` in an interactive terminal, then `donotmix apply <id>`. I did not run these. To stop the latent hazards from becoming live, add constraints to `donotmix.yaml`: `!(guest_checkout & loyalty_program)`, `!(guest_checkout & new_payment)`, and `!(new_payment & !express_shipping)`. Check the last one against the real business rule first.

## Summary
Staging is NOT safe to release. Two live checkout hazards produce wrong discount and loyalty points. One live recommendation hazard, which is on in Staging, returns an empty widget. Three more combinations (HZ-4/5/6) with guest_checkout or new_payment fail but are off, plus HZ-7 with ai_search. Nothing was changed. The cheapest fix is to switch off recommendation plus either discount_engine or dynamic_pricing after approval, then fix the code before turning them back on.
