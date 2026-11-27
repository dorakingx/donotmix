# MixMart Staging flag-combination check

Tool: `donotmix scan -e Staging` (run `20261127T025827Z`, state hash `08015f56129a5db1`). It ran 25 configurations x 2 personas (shopper, beta_tester) against the real test suite `demo/mixmart/tests/test_storefront.py`. Coverage was 100% of 2-way and 3-way flag combinations over the 512 feasible states. No flag in Flagsmith was changed. Proposals were only generated, not approved or applied.

## 1. Is current Staging safe? NO (LIVE_HAZARD)
Live state: new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on, recommendation=on, dark_mode=on; guest_checkout, express_shipping, new_payment, ai_search=off. The `beta_testers` segment override turns ai_search on for beta users.
Three hazards fail in the current state, with no diff from live. All three give silent wrong results with no exception.

## 2. Dangerous combinations (minimal core) and failing tests
| ID | Status | Minimal flag values | Failing test | Failure |
|---|---|---|---|---|
| HZ-1 | LIVE | new_checkout=on + dynamic_pricing=on + discount_engine=on | test_member_checkout_total | AssertionError: discount 920 != 992 (both personas). Sufficient: fails in 64/96 completions. |
| HZ-2 | LIVE | new_checkout + dynamic_pricing + discount_engine + loyalty_program all on | test_loyalty_points | AssertionError: points 95 != 94 (both personas). It follows from HZ-1's wrong total. |
| HZ-3 | LIVE | recommendation=on (beta_tester only; the segment override sets ai_search=on) | test_recommendations | AssertionError: recommendation widget is empty for a product with related items |
| HZ-4 | latent | express_shipping=OFF + new_payment=on | test_loyalty_points | PaymentError. Fails in all 128 completions. |
| HZ-5 | latent | guest_checkout=on + loyalty_program=on | test_guest_checkout_policy | AttributeError. Guests have no customer account, so points cannot be awarded. |
| HZ-6 | latent | guest_checkout=on + new_payment=on | test_guest_checkout_policy | PaymentError |
| HZ-7 | latent | recommendation=on + ai_search=on | test_recommendations | AssertionError. This is the same cause as HZ-3, which fires through the segment override. |

Latent means the failure is not present in the current state but is one flag flip away. Two of them (HZ-4, HZ-5/6) are close to being reachable. HZ-4 needs a flag to be off, which is easy to miss. HZ-1 is a 3-flag interaction. Per-flag tests would not catch it, and a 2-way test would also miss it. HZ-3 appears only for the beta segment.
Likely code locations: `checkout.py:47` and `discounts.py:11`/`pricing.py:10` for HZ-1/2 (the discount is computed on a different price basis than the total), `recommend.py:9` and `search.py:9` for HZ-3/7 (ai_search lowercases skus), `loyalty.py:7` for HZ-5.
Caveat: the scan is finite testing. Generality for HZ-3 and HZ-5 to HZ-7 was checked by sampling only, not exhaustively. Absence of other hazards is not a proof.

## 3. How to reproduce
From the repo root:
```
set -a; . ./.env; set +a
export PATH=$HOME/Developer/App/donotmix/.venv/bin:$PATH
donotmix scan -e Staging                                  # full scan, read-only
donotmix reproduce HZ-1 --run 20261127T025827Z            # exit 10 = still fails, 0 = no longer reproduces
```
Do the same for HZ-2 through HZ-7. Each runs `pytest` against a rewritten copy of the environment document (`.donotmix/runs/20261127T025827Z/repro/HZ-n.environment.json`), so nothing in Flagsmith is toggled. The full report is `.donotmix/runs/20261127T025827Z/report.md`. I reproduced HZ-3 and it still fails.

## Mitigation proposal for Staging (NOT applied; needs human approval)
The smallest verified change must break HZ-1, HZ-2 and HZ-3 together. Every option below needs 2 flips:
- `P-1127025859-discount_e-recommenda`: discount_engine on->off and recommendation on->off. Least disruptive, because discount_engine and recommendation are the secondary features.
- `P-1127025859-dynamic_pr-recommenda`: dynamic_pricing->off and recommendation->off.
- `P-1127025859-new_checko-recommenda`: new_checkout->off and recommendation->off. This disables the whole checkout rollout.
Each was pre-verified (the resulting state passes 2/2 runs). Recommended is the first. To apply it, a human runs `donotmix approve P-1127025859-discount_e-recommenda` in an interactive terminal, then `donotmix apply`. Do not apply it blindly. Afterwards, rescan.
Also recommended: add constraints in `donotmix.yaml` to keep the latent combinations unreachable (`excludes` for guest_checkout with new_payment and with loyalty_program, `requires` for new_payment with express_shipping, and the rule `!(new_checkout & dynamic_pricing & discount_engine)`), and fix the code. Production is a protected environment, so its state was not tested or touched.

## Summary
Staging is NOT safe to release as it stands. Three live silent-bug hazards exist: the discount/price mismatch in new_checkout+dynamic_pricing+discount_engine (which also breaks loyalty points when loyalty_program is on), and an empty recommendation widget for beta users. Four more latent hazards are one flag flip away. Nothing in Flagsmith was modified. Three mitigation proposals are waiting for human approval, with discount_engine+recommendation off as the recommended one.
