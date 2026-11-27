# DoNotMix Safety Report

> **LIVE_HAZARD** - The current Staging state fails (3 live hazard(s)); 4 latent hazard(s) more.

- Environment: **Staging** (project MixMart, http://localhost:8000), state hash `08015f56129a5db1`
- Snapshot source: flagsmith-cli (live), fetched 2026-11-27T02:36:24+00:00
- Analysis mode: rule-based
- Plan: risk (t=3, seed=0), 25 configurations x 2 personas
- Feasible configurations: 512 of 1024
- Coverage: 2-way 100.0% (177/177), 3-way 100.0% (914/914)
- Executions: 50 plan + 464 minimization, 5.26s execution, 73.79s total

_Finite testing: a clean result means no failure was observed under the executed configurations, tests and personas. It is not a proof of safety._

## Hazards (7)

### HZ-1 [LIVE / critical] new_checkout=on + dynamic_pricing=on + discount_engine=on -> test_member_checkout_total (AssertionError)

- **Minimal failing condition (core):** `new_checkout=on`, `dynamic_pricing=on`, `discount_engine=on`
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from nearest-passing-config: `new_checkout=on`, `discount_engine=on` (globally-minimal)
- Generality: **sufficient** - fails in 64/96 completions (exhaustive), masked 32
- Personas: beta_tester, shopper; class: silent-wrong-result
- Diff from live state to reproduce: (none)
- Extra tests spent minimizing: 106
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_member_checkout_total` [AssertionError] (beta_tester): AssertionError: discount 920 != 992
  - `demo.mixmart.tests.test_storefront::test_member_checkout_total` [AssertionError] (shopper): AssertionError: discount 920 != 992

Reproduce:
```bash
donotmix reproduce HZ-1 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-1.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when new_checkout=on, dynamic_pricing=on, discount_engine=on hold together (persona: beta_tester, shopper).
- [verified] Each of these 3 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Sufficient: the failure occurs in all 96 feasible completions of the other flags.
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via module, scope edges, e.g. demo/mixmart/mixmart/flags.py (module); demo/mixmart/mixmart/ui.py (module).
- [heuristic] No exception is raised: the system returns a wrong business result, which per-flag tests did not catch.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-1 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(new_checkout & dynamic_pricing & discount_engine)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/checkout.py:47 (checkout): check every place where the code paths of new_checkout, dynamic_pricing, discount_engine read or write shared state.

Related code:
`demo/mixmart/mixmart/checkout.py:47` (checkout)
```
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
  49 |     return _legacy_checkout(cart, customer, features)
  50 | 
```
`demo/mixmart/mixmart/discounts.py:11` (discount_for)
```
   9 | def discount_for(priced_lines, features: Features) -> int:
  10 |     """priced_lines: iterable of (sku, qty, unit_price)."""
  11 |     if not features.on("discount_engine"):
  12 |         return 0
  13 |     subtotal = sum(q * u for _, q, u in priced_lines)
  14 |     return subtotal * PERCENT // 100 if subtotal >= THRESHOLD else 0
```
`demo/mixmart/mixmart/pricing.py:10` (unit_price)
```
   8 | 
   9 | def unit_price(product: Product, features: Features) -> int:
  10 |     if features.on("dynamic_pricing") and product.high_demand:
  11 |         return product.list_price * (100 + SURGE_PERCENT) // 100
  12 |     return product.list_price
  13 | 
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```

### HZ-2 [LIVE / critical] new_checkout=on + dynamic_pricing=on + discount_engine=on + loyalty_program=on -> test_loyalty_points (AssertionError)

- **Minimal failing condition (core):** `new_checkout=on`, `dynamic_pricing=on`, `discount_engine=on`, `loyalty_program=on`
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from nearest-passing-config: `new_checkout=on`, `discount_engine=on` (globally-minimal)
- Generality: **sufficient** - fails in 32/48 completions (exhaustive), masked 16
- Personas: beta_tester, shopper; class: silent-wrong-result
- Diff from live state to reproduce: (none)
- Extra tests spent minimizing: 0
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_loyalty_points` [AssertionError] (beta_tester): AssertionError: points 95 != 94
  - `demo.mixmart.tests.test_storefront::test_loyalty_points` [AssertionError] (shopper): AssertionError: points 95 != 94

Reproduce:
```bash
donotmix reproduce HZ-2 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-2.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when new_checkout=on, dynamic_pricing=on, discount_engine=on, loyalty_program=on hold together (persona: beta_tester, shopper).
- [verified] Each of these 4 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Sufficient: the failure occurs in all 48 feasible completions of the other flags.
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via module, scope edges, e.g. demo/mixmart/mixmart/flags.py (module); demo/mixmart/mixmart/ui.py (module).
- [heuristic] No exception is raised: the system returns a wrong business result, which per-flag tests did not catch.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-2 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(new_checkout & dynamic_pricing & discount_engine & loyalty_program)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/checkout.py:47 (checkout): check every place where the code paths of new_checkout, dynamic_pricing, discount_engine, loyalty_program read or write shared state.

Related code:
`demo/mixmart/mixmart/checkout.py:47` (checkout)
```
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
  49 |     return _legacy_checkout(cart, customer, features)
  50 | 
```
`demo/mixmart/mixmart/discounts.py:11` (discount_for)
```
   9 | def discount_for(priced_lines, features: Features) -> int:
  10 |     """priced_lines: iterable of (sku, qty, unit_price)."""
  11 |     if not features.on("discount_engine"):
  12 |         return 0
  13 |     subtotal = sum(q * u for _, q, u in priced_lines)
  14 |     return subtotal * PERCENT // 100 if subtotal >= THRESHOLD else 0
```
`demo/mixmart/mixmart/loyalty.py:7` (award_points)
```
   5 | 
   6 | def award_points(customer, total: int, features: Features) -> int:
   7 |     if not features.on("loyalty_program"):
   8 |         return 0
   9 |     points = total // 100
  10 |     customer.points += points  # customer is an account; see checkout for who can reach here
```
`demo/mixmart/mixmart/pricing.py:10` (unit_price)
```
   8 | 
   9 | def unit_price(product: Product, features: Features) -> int:
  10 |     if features.on("dynamic_pricing") and product.high_demand:
  11 |         return product.list_price * (100 + SURGE_PERCENT) // 100
  12 |     return product.list_price
  13 | 
```

### HZ-3 [LIVE / critical] recommendation=on -> test_recommendations (AssertionError)

- **Minimal failing condition (core):** `recommendation=on`
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from nearest-passing-config: `recommendation=on` (globally-minimal)
- Generality: **held-in-sample** - fails in 32/32 completions (sampled)
- Personas: beta_tester; class: silent-wrong-result
- Diff from live state to reproduce: (none)
- Extra tests spent minimizing: 39
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_recommendations` [AssertionError] (beta_tester): AssertionError: recommendation widget is empty for a product with related items

Reproduce:
```bash
donotmix reproduce HZ-3 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-3.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails whenever recommendation=on (persona: beta_tester).
- [verified] Each of these 1 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Held in all 32 sampled completions of the other flags (sample, not exhaustive).
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via call, module edges, e.g. demo/mixmart/mixmart/flags.py (module); also_bought (also_bought -> callees reading ['ai_search']).
- [heuristic] No exception is raised: the system returns a wrong business result, which per-flag tests did not catch.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-3 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(recommendation)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/recommend.py:9 (also_bought): check every place where the code paths of recommendation read or write shared state.

Related code:
`demo/mixmart/mixmart/recommend.py:9` (also_bought)
```
   7 | 
   8 | def also_bought(sku: str, features: Features, limit: int = 3) -> list[str]:
   9 |     if not features.on("recommendation"):
  10 |         return []
  11 |     product = get(sku)
  12 |     if product is None:
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```
`demo/mixmart/mixmart/ui.py:11` (theme)
```
   9 |         "bg": "#111111" if dark else "#fafaf5",
  10 |         "fg": "#f5f1e6" if dark else "#111111",
  11 |         "badges": [n for n in ("new_checkout", "express_shipping", "loyalty_program", "ai_search") if features.on(n)],
  12 |     }
```

### HZ-4 [LATENT / high] express_shipping=off + new_payment=on -> test_loyalty_points (PaymentError)

- **Minimal failing condition (core):** `express_shipping=off`, `new_payment=on`
- Implied by constraints: `new_checkout=on` (new-payment-needs-new-checkout)
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from nearest-passing-config: `new_checkout=on`, `new_payment=on` (globally-minimal)
- Generality: **sufficient** - fails in 128/128 completions (exhaustive)
- Personas: beta_tester, shopper; class: crash
- Diff from live state to reproduce: `new_payment=on`
- Extra tests spent minimizing: 243
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_loyalty_points` [PaymentError] (beta_tester): mixmart.payments.PaymentError: new gateway requires shipping_method
  - `demo.mixmart.tests.test_storefront::test_member_checkout_total` [PaymentError] (beta_tester): mixmart.payments.PaymentError: new gateway requires shipping_method
  - `demo.mixmart.tests.test_storefront::test_loyalty_points` [PaymentError] (shopper): mixmart.payments.PaymentError: new gateway requires shipping_method
  - `demo.mixmart.tests.test_storefront::test_member_checkout_total` [PaymentError] (shopper): mixmart.payments.PaymentError: new gateway requires shipping_method

Reproduce:
```bash
donotmix reproduce HZ-4 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-4.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when express_shipping=off, new_payment=on hold together (persona: beta_tester, shopper).
- [verified] Each of these 2 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Sufficient: the failure occurs in all 128 feasible completions of the other flags.
- [verified] Implied by constraints: new_checkout (constraint new-payment-needs-new-checkout).
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via module, scope edges, e.g. demo/mixmart/mixmart/flags.py (module); demo/mixmart/mixmart/ui.py (module).
- [heuristic] The combination raises PaymentError.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-4 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(!express_shipping & new_payment)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/checkout.py:47 (checkout): check every place where the code paths of express_shipping, new_payment read or write shared state.

Related code:
`demo/mixmart/mixmart/checkout.py:47` (checkout)
```
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
  49 |     return _legacy_checkout(cart, customer, features)
  50 | 
```
`demo/mixmart/mixmart/payments.py:13` (create_intent)
```
  11 |     if amount <= 0:
  12 |         raise PaymentError("amount must be positive")
  13 |     if features.on("new_payment"):
  14 |         if not shipping_method:
  15 |             raise PaymentError("new gateway requires shipping_method")
  16 |         return {"gateway": "nova", "amount": amount, "currency": "USD", "shipping_method": shipping_method}
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```
`demo/mixmart/mixmart/shipping.py:10` (select)
```
   8 | def select(requested: str, features: Features) -> tuple[str | None, int]:
   9 |     """Return (method_name, fee). Method names were introduced together with express shipping."""
  10 |     if features.on("express_shipping"):
  11 |         if requested == "express":
  12 |             return "express", EXPRESS_FEE
  13 |         return "standard", STANDARD_FEE
```

### HZ-5 [LATENT / high] guest_checkout=on + loyalty_program=on -> test_guest_checkout_policy (AttributeError)

- **Minimal failing condition (core):** `guest_checkout=on`, `loyalty_program=on`
- Implied by constraints: `express_shipping=off` (express-needs-new-checkout, no-express-for-guests)
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from live-environment: `guest_checkout=on` (globally-minimal)
- Generality: **held-in-sample** - fails in 24/32 completions (sampled), masked 8
- Personas: beta_tester, shopper; class: crash
- Diff from live state to reproduce: `guest_checkout=on`
- Extra tests spent minimizing: 37
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_guest_checkout_policy` [AttributeError] (beta_tester): AttributeError: 'NoneType' object has no attribute 'points'
  - `demo.mixmart.tests.test_storefront::test_guest_checkout_policy` [AttributeError] (shopper): AttributeError: 'NoneType' object has no attribute 'points'

Reproduce:
```bash
donotmix reproduce HZ-5 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-5.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when guest_checkout=on, loyalty_program=on hold together (persona: beta_tester, shopper).
- [verified] Each of these 2 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Held in all 32 sampled completions of the other flags (sample, not exhaustive).
- [verified] Implied by constraints: express_shipping (constraint express-needs-new-checkout, no-express-for-guests).
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via module, scope edges, e.g. demo/mixmart/mixmart/flags.py (module); demo/mixmart/mixmart/ui.py (module).
- [heuristic] The combination raises AttributeError.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-5 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(guest_checkout & loyalty_program)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/checkout.py:45 (checkout): check every place where the code paths of guest_checkout, loyalty_program read or write shared state.

Related code:
`demo/mixmart/mixmart/checkout.py:45` (checkout)
```
  43 | 
  44 | def checkout(cart: dict[str, int], customer: Customer | None, features: Features, shipping_choice: str = "standard") -> Order:
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
```
`demo/mixmart/mixmart/loyalty.py:7` (award_points)
```
   5 | 
   6 | def award_points(customer, total: int, features: Features) -> int:
   7 |     if not features.on("loyalty_program"):
   8 |         return 0
   9 |     points = total // 100
  10 |     customer.points += points  # customer is an account; see checkout for who can reach here
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```
`demo/mixmart/mixmart/shipping.py:10` (select)
```
   8 | def select(requested: str, features: Features) -> tuple[str | None, int]:
   9 |     """Return (method_name, fee). Method names were introduced together with express shipping."""
  10 |     if features.on("express_shipping"):
  11 |         if requested == "express":
  12 |             return "express", EXPRESS_FEE
  13 |         return "standard", STANDARD_FEE
```

### HZ-6 [LATENT / high] guest_checkout=on + new_payment=on -> test_guest_checkout_policy (PaymentError)

- **Minimal failing condition (core):** `guest_checkout=on`, `new_payment=on`
- Implied by constraints: `new_checkout=on` (new-payment-needs-new-checkout), `express_shipping=off` (no-express-for-guests)
- Flagsmith overrides for this persona: `ai_search=on`
- Trigger from live-environment: `guest_checkout=on`, `new_payment=on` (globally-minimal)
- Generality: **held-in-sample** - fails in 32/32 completions (sampled)
- Personas: beta_tester, shopper; class: crash
- Diff from live state to reproduce: `guest_checkout=on`, `new_payment=on`
- Extra tests spent minimizing: 4
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_guest_checkout_policy` [PaymentError] (beta_tester): mixmart.payments.PaymentError: new gateway requires shipping_method
  - `demo.mixmart.tests.test_storefront::test_guest_checkout_policy` [PaymentError] (shopper): mixmart.payments.PaymentError: new gateway requires shipping_method

Reproduce:
```bash
donotmix reproduce HZ-6 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-6.environment.json DONOTMIX_IDENTITY=beta-1 DONOTMIX_TRAITS='{"beta": true}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when guest_checkout=on, new_payment=on hold together (persona: beta_tester, shopper).
- [verified] Each of these 2 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Held in all 32 sampled completions of the other flags (sample, not exhaustive).
- [verified] Implied by constraints: new_checkout (constraint new-payment-needs-new-checkout), express_shipping (constraint no-express-for-guests).
- [verified] For persona beta_tester, Flagsmith evaluates ai_search=on despite the environment default (segment override: beta_testers).
- [heuristic] Static analysis links these flags via module, scope edges, e.g. demo/mixmart/mixmart/checkout.py (module); demo/mixmart/mixmart/checkout.py:44 (function checkout).
- [heuristic] The combination raises PaymentError.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-6 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(guest_checkout & new_payment)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/checkout.py:45 (checkout): check every place where the code paths of guest_checkout, new_payment read or write shared state.

Related code:
`demo/mixmart/mixmart/checkout.py:45` (checkout)
```
  43 | 
  44 | def checkout(cart: dict[str, int], customer: Customer | None, features: Features, shipping_choice: str = "standard") -> Order:
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
```
`demo/mixmart/mixmart/checkout.py:47` (checkout)
```
  45 |     if customer is None and not features.on("guest_checkout"):
  46 |         raise CheckoutError("login required")
  47 |     if features.on("new_checkout"):
  48 |         return _new_checkout(cart, customer, features, shipping_choice)
  49 |     return _legacy_checkout(cart, customer, features)
  50 | 
```
`demo/mixmart/mixmart/payments.py:13` (create_intent)
```
  11 |     if amount <= 0:
  12 |         raise PaymentError("amount must be positive")
  13 |     if features.on("new_payment"):
  14 |         if not shipping_method:
  15 |             raise PaymentError("new gateway requires shipping_method")
  16 |         return {"gateway": "nova", "amount": amount, "currency": "USD", "shipping_method": shipping_method}
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```

### HZ-7 [LATENT / medium] recommendation=on + ai_search=on -> test_recommendations (AssertionError)

- **Minimal failing condition (core):** `recommendation=on`, `ai_search=on`
- Trigger from live-environment: `ai_search=on` (globally-minimal)
- Generality: **held-in-sample** - fails in 32/32 completions (sampled)
- Personas: shopper; class: silent-wrong-result
- Diff from live state to reproduce: `ai_search=on`
- Extra tests spent minimizing: 35
- Failing tests:
  - `demo.mixmart.tests.test_storefront::test_recommendations` [AssertionError] (shopper): AssertionError: recommendation widget is empty for a product with related items

Reproduce:
```bash
donotmix reproduce HZ-7 --run 20261127T023622Z
DONOTMIX_ENV_DOCUMENT=benchmarks/results/mixmart/runs/20261127T023622Z/repro/HZ-7.environment.json DONOTMIX_IDENTITY=shopper-1 DONOTMIX_TRAITS='{"beta": false}' python -m pytest demo/mixmart/tests/test_storefront.py -q -p no:cacheprovider -p no:randomly
```

Analysis (rule-based):
- [verified] Fails when recommendation=on, ai_search=on hold together (persona: shopper).
- [verified] Each of these 2 literal(s) is necessary: flipping any one of them makes the failing test pass.
- [verified] Held in all 32 sampled completions of the other flags (sample, not exhaustive).
- [heuristic] Static analysis links these flags via call, module edges, e.g. demo/mixmart/mixmart/flags.py (module); also_bought (also_bought -> callees reading ['ai_search']).
- [heuristic] No exception is raised: the system returns a wrong business result, which per-flag tests did not catch.
- Suggested next steps:
  - Add a regression test pinned to this combination: `donotmix reproduce HZ-7 --run 20261127T023622Z`.
  - Until the code is fixed, keep the combination unreachable: either switch one of the core flags off in the affected environment (DoNotMix can propose and verify this) or declare a constraint `!(recommendation & ai_search)` in donotmix.yaml.
  - Start at demo/mixmart/mixmart/recommend.py:9 (also_bought): check every place where the code paths of recommendation, ai_search read or write shared state.

Related code:
`demo/mixmart/mixmart/recommend.py:9` (also_bought)
```
   7 | 
   8 | def also_bought(sku: str, features: Features, limit: int = 3) -> list[str]:
   9 |     if not features.on("recommendation"):
  10 |         return []
  11 |     product = get(sku)
  12 |     if product is None:
```
`demo/mixmart/mixmart/search.py:9` (search)
```
   7 | def search(query: str, features: Features) -> list[str]:
   8 |     q = query.lower()
   9 |     if features.on("ai_search"):
  10 |         # semantic index stores normalised ids
  11 |         return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
  12 |     return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
```
`demo/mixmart/mixmart/ui.py:11` (theme)
```
   9 |         "bg": "#111111" if dark else "#fafaf5",
  10 |         "fg": "#f5f1e6" if dark else "#111111",
  11 |         "badges": [n for n in ("new_checkout", "express_shipping", "loyalty_program", "ai_search") if features.on(n)],
  12 |     }
```

## AI usage

- Mode: rule-based; calls 0; tokens in/out 0/0

## Constraints

- `express-needs-new-checkout`: `express_shipping -> (new_checkout)` - express shipping is only implemented in the new checkout flow
- `new-payment-needs-new-checkout`: `new_payment -> (new_checkout)` - the new payment gateway is only wired into the new checkout
- `no-express-for-guests`: `!(express_shipping & guest_checkout)` - express shipping needs a customer account

Generated by DoNotMix 0.1.0 (commit bbc5147), run 20261127T023622Z.
