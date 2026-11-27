# MixMart Staging pre-release flag-combination check

Tool: `donotmix scan -e Staging` (read-only snapshot of live Flagsmith, 25 configs x 2 personas, 100% 2-way and 3-way coverage, 514 real test executions). Run ID `20261127T025836Z`, report at `.donotmix/runs/20261127T025836Z/report.md`. No flag was changed. I ran `donotmix propose`, which does not apply anything.

## 1. Is current Staging safe?
**No. It is in a LIVE_HAZARD state: 3 hazards fail right now, and 4 more are latent.**

Live Staging flags:

| Flag | State |
|---|---|
| new_checkout | on |
| dynamic_pricing | on |
| discount_engine | on |
| loyalty_program | on |
| recommendation | on |
| dark_mode | on |
| guest_checkout | off |
| express_shipping | off |
| new_payment | off |
| ai_search | off (the `beta_testers` segment override turns it on for beta users) |

## 2. Dangerous combinations (minimal core) and failing tests

| ID | Status | Minimal flag values | Failing test | Symptom | Generality |
|---|---|---|---|---|---|
| HZ-1 | LIVE | new_checkout=on, dynamic_pricing=on, discount_engine=on | `test_member_checkout_total` | Wrong total with no exception: discount 920 != 992 | Sufficient (exhaustive, fails 64/96 completions; 32 masked by other flags) |
| HZ-2 | LIVE | new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on | `test_loyalty_points` | Wrong points: 95 != 94 (a knock-on of HZ-1 pricing) | Sufficient (exhaustive) |
| HZ-3 | LIVE, beta_tester persona only | recommendation=on (the segment override also sets ai_search=on for beta users) | `test_recommendations` | Recommendation widget is empty for a product with related items | Held in sample (32/32); sampled, not exhaustive |
| HZ-4 | LATENT | express_shipping=OFF, new_payment=on (a flag has to be off) | `test_loyalty_points` and `test_member_checkout_total` | PaymentError: new gateway requires shipping_method | Sufficient (128/128) |
| HZ-5 | LATENT | guest_checkout=on, loyalty_program=on | `test_guest_checkout_policy` | AttributeError: 'NoneType' has no attribute 'points' (loyalty points awarded to a guest) | Held in sample (24/32; 8 masked) |
| HZ-6 | LATENT | guest_checkout=on, new_payment=on | `test_guest_checkout_policy` | PaymentError: new gateway requires shipping_method | Held in sample (32/32) |
| HZ-7 | LATENT | recommendation=on, ai_search=on (shopper persona) | `test_recommendations` | Empty recommendation widget | Held in sample (32/32) |

HZ-3 and HZ-7 are the same bug (recommendation together with ai_search). HZ-3 is live because the segment override gives beta users ai_search=on. Turning ai_search on globally would make it hit every user.

The constraints declared in `donotmix.yaml` were respected: express_shipping and new_payment require new_checkout, and express_shipping excludes guest_checkout. 512 of 1024 states are feasible.

Caveat: the pass/fail result is finite testing, not a proof. HZ-3 and HZ-5 to HZ-7 have sampled rather than exhaustive generality.

## 3. How to reproduce
Run each of these from the repo root, with the Flagsmith credentials loaded (`set -a; . ./.env; set +a`) and the venv on PATH (`PATH=$HOME/Developer/App/donotmix/.venv/bin:$PATH`):

```bash
donotmix reproduce HZ-<n> --run 20261127T025836Z     # exit 10 = still fails, 0 = no longer reproduces
```
I ran all seven. Each exited 10, so each still reproduces.

Without donotmix, each hazard has a stored environment document at `.donotmix/runs/20261127T025836Z/repro/HZ-<n>.environment.json`:
```bash
DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T025836Z/repro/HZ-1.environment.json \
DONOTMIX_IDENTITY=shopper-1 DONOTMIX_TRAITS='{"beta": false}' \
python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider
```
- Use `DONOTMIX_IDENTITY=shopper-1` with `DONOTMIX_TRAITS='{"beta": false}'` for HZ-1, HZ-2 and HZ-4 to HZ-7.
- Use `beta-1` with `'{"beta": true}'` for HZ-3.
- HZ-1 and HZ-2 need no flag change, because live Staging already has them.
- HZ-4 to HZ-7 need a flag change away from the live state: new_payment=on for HZ-4, guest_checkout=on for HZ-5, guest_checkout=on plus new_payment=on for HZ-6, ai_search=on for HZ-7.

Likely code locations (from static analysis; the tool labels these heuristic):
- `demo/mixmart/mixmart/checkout.py:47`
- `pricing.py` (surge pricing)
- `discounts.py` (discount threshold)
- `loyalty.py` (points awarded to a customer who may be None)
- `payments.py` (new gateway needs a shipping method)

## Proposed mitigation for Staging (NOT applied; needs human approval)
`donotmix propose` produced pre-verified proposals. Each one resolves HZ-1, HZ-2 and HZ-3, and the resulting state passed 2/2 test runs:
- **Option A:** discount_engine on -> off, recommendation on -> off
- **Option B:** dynamic_pricing on -> off, recommendation on -> off
- **Option C:** new_checkout on -> off, recommendation on -> off

My recommendation is Option A or B, the least disruptive. Option C disables the whole new checkout.
- Whichever option is chosen, recommendation has to go off, or ai_search has to stay unreachable. Otherwise HZ-3 persists.
- Option A: P-1127025944-discount_e-recommenda.
- Option B: P-1127025944-dynamic_pr-recommenda.
- Option C: P-1127025944-new_checko-recommenda.

To apply one, a human runs `donotmix approve <id>` and then `donotmix apply <id>`. Approval needs an interactive terminal.

The latent hazards are not live yet. Before release, add these constraints to `donotmix.yaml`, or fix the code, so nobody flips a flag into them:
- `!(!express_shipping & new_payment)`, that is, new_payment requires express_shipping, or fix the missing shipping_method.
- `!(guest_checkout & loyalty_program)`
- `!(guest_checkout & new_payment)`
- `!(recommendation & ai_search)`

The proper fix is in the code: the surge price and discount threshold math, the guest case in `award_points`, the shipping_method requirement in the new payment gateway, and the recommendation widget under ai_search.

## Summary
Staging is **not** safe to release. Three hazards are live:
- HZ-1: new_checkout + dynamic_pricing + discount_engine gives a wrong discount silently.
- HZ-2: the same three flags plus loyalty_program give wrong loyalty points.
- HZ-3: recommendation gives an empty widget, hit by the beta segment through its ai_search override.

Four more are latent: new_payment without express_shipping, guest_checkout with loyalty_program, guest_checkout with new_payment, and recommendation with ai_search. The mitigation is turning off recommendation plus one of discount_engine, dynamic_pricing or new_checkout. It is proposed only, and I changed no flag.
