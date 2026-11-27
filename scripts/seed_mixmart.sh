#!/usr/bin/env bash
# Seed a Flagsmith instance with the MixMart demo project using ONLY the official Flagsmith CLI.
# Idempotent: "already exists" errors are ignored. Requires an Admin API key for the target host
# (e.g. FLAGSMITH_API_KEY_localhost_8000) and FLAGSMITH_API_URL.
set -uo pipefail
ORG="${FLAGSMITH_ORGANISATION:-DoNotMix Demo}"
export FLAGSMITH_PROJECT="${FLAGSMITH_PROJECT:-MixMart}"
FS="flagsmith --no-input"
FLAGS=(new_checkout dynamic_pricing discount_engine guest_checkout express_shipping loyalty_program new_payment recommendation ai_search dark_mode)

$FS project get "$FLAGSMITH_PROJECT" --json >/dev/null 2>&1 || $FS project create "$FLAGSMITH_PROJECT" --organisation "$ORG"
for e in Development Staging Production; do
  $FS environment get "$e" --json >/dev/null 2>&1 || $FS environment create "$e" --description "MixMart $e"
done
for f in "${FLAGS[@]}"; do
  $FS feature get "$f" --json >/dev/null 2>&1 || $FS feature create "$f" --description "MixMart demo flag"
done
$FS segment get beta_testers --json >/dev/null 2>&1 || $FS segment create beta_testers \
  --description "Opt-in beta cohort (trait beta=true)" \
  --rules '{"type":"ALL","rules":[{"type":"ANY","conditions":[{"property":"beta","operator":"EQUAL","value":"true"}]}]}'

envkey() { $FS environment get "$1" --jq .api_key; }
STAGING=$(envkey Staging); PRODUCTION=$(envkey Production); DEVELOPMENT=$(envkey Development)

set_state() { # env_key flag on|off
  if [ "$3" = on ]; then $FS flag enable "$2" -e "$1" --yes >/dev/null; else $FS flag disable "$2" -e "$1" --yes >/dev/null; fi
}
# Staging: a release candidate where several new features were switched on by different teams.
for f in new_checkout dynamic_pricing discount_engine loyalty_program recommendation dark_mode; do set_state "$STAGING" "$f" on; done
for f in guest_checkout express_shipping new_payment ai_search; do set_state "$STAGING" "$f" off; done
# ai_search is only rolled out to the beta segment in Staging.
$FS flag update ai_search --enable --segment beta_testers -e "$STAGING" --yes >/dev/null 2>&1 || true
# Production: legacy flows only.
for f in recommendation dark_mode; do set_state "$PRODUCTION" "$f" on; done
for f in new_checkout dynamic_pricing discount_engine guest_checkout express_shipping loyalty_program new_payment ai_search; do set_state "$PRODUCTION" "$f" off; done
# Development: everything new on, for developers.
for f in new_checkout dynamic_pricing guest_checkout new_payment ai_search; do set_state "$DEVELOPMENT" "$f" on; done

echo "Seeded project $FLAGSMITH_PROJECT"
echo "  Staging     $STAGING"
echo "  Production  $PRODUCTION"
echo "  Development $DEVELOPMENT"
