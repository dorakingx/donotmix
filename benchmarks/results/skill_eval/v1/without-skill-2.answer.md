# MixMart Staging flag-combination check

Tool: `donotmix scan` (risk strategy, t=3, seed 0). It ran 25 configurations x 2 personas against the real storefront tests, then minimized each failure. Run id `20261127T025252Z`, report at `.donotmix/runs/20261127T025252Z/report.md`. No Flagsmith flag was changed. `propose` only prints proposals; I did not run `approve` or `apply`.

## 1. Is Staging safe? No (LIVE_HAZARD)
The current Staging state fails 3 hazards. Four more are latent and would be triggered by flipping one more flag.

Live state: new_checkout, dynamic_pricing, discount_engine, loyalty_program, recommendation and dark_mode are ON. ai_search is OFF in the environment but ON for the `beta_testers` segment. guest_checkout, express_shipping and new_payment are OFF.

## 2. Dangerous combinations (minimal sets)

| ID | Status | Minimal flag values | Failing test | Failure |
|---|---|---|---|---|
| HZ-1 | LIVE, critical | new_checkout=on + dynamic_pricing=on + discount_engine=on | `test_member_checkout_total` | `discount 920 != 992` (silent wrong discount). Every other flag setting also fails (64/96 completions, 32 masked). Affects shopper and beta_tester. |
| HZ-2 | LIVE, critical | new_checkout + dynamic_pricing + discount_engine + loyalty_program, all on | `test_loyalty_points` | `points 95 != 94` (silent wrong points, 32/48 completions). Affects both personas. |
| HZ-3 | LIVE, critical | tool reports `recommendation=on`. The beta persona also has ai_search=on through the `beta_testers` segment override, so effectively recommendation=on + ai_search=on | `test_recommendations` | `recommendation widget is empty for a product with related items`. Only the beta_tester persona fails today. |
| HZ-4 | latent, high | new_payment=on with express_shipping=off (new_checkout must be on, which it is) | `test_loyalty_points`, `test_member_checkout_total` | `PaymentError: new gateway requires shipping_method`. Fails in 128/128 completions. |
| HZ-5 | latent, high | guest_checkout=on + loyalty_program=on | `test_guest_checkout_policy` | `AttributeError: 'NoneType' object has no attribute 'points'`. Loyalty points are awarded to a guest with no account. |
| HZ-6 | latent, high | guest_checkout=on + new_payment=on | `test_guest_checkout_policy` | `PaymentError: new gateway requires shipping_method`. |
| HZ-7 | latent, medium | recommendation=on + ai_search=on (environment-wide) | `test_recommendations` | Same empty widget. Fails for the shopper persona once ai_search is turned on for everyone. |

Caveats:
- HZ-1 and HZ-2 are silent wrong results: no exception, only wrong money and points.
- HZ-3, HZ-5, HZ-6 and HZ-7 are "held-in-sample". They were sampled, not exhaustively verified.
- HZ-3 and HZ-7 are the same bug (recommendation + ai_search). Today it is live only for beta testers.
- A clean result would not prove safety. Testing is finite.

## 3. How to reproduce
Run these from the repo directory after `set -a; . ./.env; set +a`, with the venv on PATH. Each command re-runs the minimal case and exits 10 if it still fails:

```bash
donotmix reproduce HZ-1 --run 20261127T025252Z   # likewise HZ-2 ... HZ-7
```

The live hazards HZ-1 to HZ-3 need no flag change. To reproduce HZ-1 by hand:

```bash
DONOTMIX_ENV_DOCUMENT=.donotmix/runs/20261127T025252Z/repro/HZ-1.environment.json \
DONOTMIX_IDENTITY=shopper-1 DONOTMIX_TRAITS='{"beta": false}' \
python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

For HZ-3 use `HZ-3.environment.json`, `DONOTMIX_IDENTITY=beta-1` and `DONOTMIX_TRAITS='{"beta": true}'`. The other hazards use the matching `repro/HZ-N.environment.json`.

## Proposed Staging mitigation (needs human approval; nothing applied)
`donotmix propose` produced three pre-verified proposals. Each resolves HZ-1, HZ-2 and HZ-3, and each passed 2/2 verification runs. Each switches `recommendation` off plus one of the other three flags:
- `recommendation` off + `discount_engine` off (`P-1127025522-discount_e-recommenda`)
- `recommendation` off + `dynamic_pricing` off (`P-1127025522-dynamic_pr-recommenda`)
- `recommendation` off + `new_checkout` off (`P-1127025522-new_checko-recommenda`)

My suggestion is the first one, because it removes the least functionality (discounts, not checkout). I haven't judged business impact, so the choice is yours. Approval is a human step: `donotmix approve <id>`, then `donotmix apply`. Do not turn on new_payment, guest_checkout or ai_search in Staging until the latent hazards are fixed.

Alternatives and follow-ups:
- For HZ-3 only, turn `ai_search` off for the `beta_testers` segment instead of turning `recommendation` off. That would not fix HZ-1 or HZ-2.
- Add constraints to `donotmix.yaml` so these combinations stay unreachable: `!(new_checkout & dynamic_pricing & discount_engine)`, `!(recommendation & ai_search)`, `new_payment requires express_shipping`, and `guest_checkout excludes loyalty_program`.
- The real fixes are code changes: use the same surge-adjusted price for the discount in checkout.py, discounts.py and pricing.py; round loyalty points on the same basis; skip points for guests; make the new gateway default a shipping_method; and have recommend.py also_bought handle ai_search.

## Summary
Staging is not safe. Three hazards are live today: wrong discount (HZ-1), wrong loyalty points (HZ-2), and an empty recommendation widget for beta testers (HZ-3). Four more are latent: new_payment without express_shipping, guest_checkout with loyalty_program, guest_checkout with new_payment, and recommendation with ai_search. I changed nothing in Flagsmith. The proposals above await human approval.
