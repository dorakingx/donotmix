# MixMart Staging flag-combination check (pre-release)

Tool: `donotmix scan -e Staging` (risk strategy, t=3). Flagsmith was only read; **no flag was changed**.
Coverage: 25 configs x 2 personas (shopper, beta_tester), 100% 2-way and 3-way (feasible 512 of 1024 states), 514 executions. Run id `20261127T025849Z`.
Finite testing: this shows failures were found, not that unlisted states are safe.

## 1. Is current Staging safe? NO (LIVE_HAZARD)
Live Staging: new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on, recommendation=on, dark_mode=on; guest_checkout, express_shipping, new_payment, ai_search = off. Segment `beta_testers` overrides ai_search=on for beta users.
The live state already triggers 3 hazards (HZ-1, HZ-2, HZ-3). 4 more are latent (one flag flip away).

## 2. Dangerous combinations (minimal core) and failing tests

| ID | Status | Minimal flag values | Failing test | Failure |
|---|---|---|---|---|
| HZ-1 | LIVE | new_checkout=on + dynamic_pricing=on + discount_engine=on | test_member_checkout_total | AssertionError: discount 920 != 992 (silent wrong total; sufficient, fails in 64/96 completions) |
| HZ-2 | LIVE | HZ-1's three flags + loyalty_program=on | test_loyalty_points | AssertionError: points 95 != 94 (consequence of the wrong total) |
| HZ-3 | LIVE | recommendation=on (beta_tester persona; the beta_testers segment turns ai_search on) | test_recommendations | AssertionError: recommendation widget is empty for a product with related items |
| HZ-4 | latent | express_shipping=OFF + new_payment=on (needs new_checkout) | test_loyalty_points | PaymentError: new gateway requires shipping_method (fails 128/128 completions) |
| HZ-5 | latent | guest_checkout=on + loyalty_program=on | test_guest_checkout_policy | AttributeError: 'NoneType' has no attribute 'points' (guest has no account) |
| HZ-6 | latent | guest_checkout=on + new_payment=on | test_guest_checkout_policy | PaymentError: new gateway requires shipping_method (express is excluded for guests) |
| HZ-7 | latent | recommendation=on + ai_search=on (shopper) | test_recommendations | same empty widget (this is HZ-3 for non-beta users if ai_search is switched on env-wide) |

HZ-3 and HZ-7 and HZ-4/HZ-6 share root causes (ai_search with recommendation; new_payment without shipping_method). HZ-5/HZ-6/HZ-4 are not live now. HZ-3, 5, 6, 7 were confirmed on sampled completions, not exhaustively.

## 3. Reproduction
Run from the repo root with `set -a; . ./.env; set +a` and the venv on PATH. Each command was re-run here and still fails.
```
donotmix reproduce HZ-<n> --run 20261127T025849Z      # n = 1..7
# or directly, e.g. HZ-1 (shopper):
DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T025849Z/repro/HZ-1.environment.json \
DONOTMIX_IDENTITY=shopper-1 DONOTMIX_TRAITS='{"beta": false}' \
python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider
```
The repro environment documents are local copies, so Flagsmith is untouched. HZ-3 uses identity `beta-1`, traits `{"beta": true}`. Code to look at: demo/mixmart/mixmart/{checkout,pricing,discounts,loyalty,payments,recommend}.py.

## Mitigation proposal for Staging (NOT applied, human approval required)
`donotmix propose` produced three pre-verified options. Each resolves all live hazards HZ-1/2/3 (tested state passes):
1. **discount_engine on -> off + recommendation on -> off** (recommended: smallest business impact on checkout flow)
2. dynamic_pricing on -> off + recommendation on -> off
3. new_checkout on -> off + recommendation on -> off (heaviest: disables new checkout)

recommendation must go off in every option (HZ-3 is independent of the pricing trio). Preferred: option 1.
To apply, a human runs `donotmix approve <id>` in an interactive terminal, then `donotmix apply <id>` (drift-checked, re-verifies). Proposal ids are printed by `donotmix propose` (e.g. `P-1127025944-discount_e-recommenda`).
Also recommended: add constraints to donotmix.yaml / release rules for the latent hazards (`!(guest_checkout & loyalty_program)`, `!(guest_checkout & new_payment)`, `new_payment requires express_shipping`-style guard), and do not enable ai_search broadly while recommendation is on. Fix the code bugs before release.

## Summary
Staging is NOT safe: 3 live hazards (wrong discount total with new_checkout+dynamic_pricing+discount_engine; wrong loyalty points with the addition of loyalty_program; empty recommendations when recommendation is on and ai_search is on via the beta segment) plus 4 latent ones (new_payment without express_shipping, guest_checkout with loyalty_program or new_payment, recommendation+ai_search). Reproduce with `donotmix reproduce HZ-n --run 20261127T025849Z`. Suggested mitigation, pending human approval: turn off discount_engine and recommendation in Staging. No Flagsmith flags were modified.
