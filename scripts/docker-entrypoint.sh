#!/usr/bin/env bash
# Container entrypoint: bootstrap the bundled Flagsmith (first run), then run a DoNotMix command.
set -euo pipefail
cd /app
export FLAGSMITH_API_URL="${FLAGSMITH_API_URL:-http://flagsmith:8000}"
ENV_FILE="${DONOTMIX_ENV_FILE:-/state/flagsmith.env}"
if [ "${DONOTMIX_BOOTSTRAP:-1}" = "1" ]; then
  python scripts/bootstrap_flagsmith.py --api-url "$FLAGSMITH_API_URL" --env-file "$ENV_FILE"
fi
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
export FLAGSMITH_API_URL="${FLAGSMITH_API_URL_OVERRIDE:-$FLAGSMITH_API_URL}"
# the compose network reaches Flagsmith as http://flagsmith:8000
sed -i "s#^api_url:.*#api_url: ${FLAGSMITH_API_URL}#" donotmix.yaml
case "${1:-dashboard}" in
  dashboard)
    donotmix scan || true   # first scan so the dashboard has data (exit code 11 = live hazard, expected for the demo)
    DONOTMIX_DASHBOARD_ALLOW_REMOTE=1 exec donotmix dashboard --host 0.0.0.0 --port 8765 ;;
  *) exec donotmix "$@" ;;
esac
