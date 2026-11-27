#!/usr/bin/env bash
# Fast path for "is ENV safe to release?": preflight + scan + (if the live state fails) verified
# mitigation proposals, printed as ONE compact JSON object. Never changes Flagsmith.
# Usage: release_check.sh [ENVIRONMENT]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_NAME="${1:-}"
pre=$(bash "$HERE/preflight.sh" "$ENV_NAME")
if ! echo "$pre" | grep -q '"config":true'; then
  echo "{\"preflight\":$pre,\"next\":\"no donotmix.yaml: run donotmix init, then see references/config.md\"}"; exit 3
fi
if ! echo "$pre" | grep -q '"auth":true'; then echo "{\"preflight\":$pre,\"next\":\"fix Flagsmith credentials (references/flagsmith-cli.md)\"}"; exit 4; fi
brief=$(donotmix scan ${ENV_NAME:+-e "$ENV_NAME"} --brief 2>/dev/null); code=$?
props="[]"
if [ "$code" -eq 11 ]; then
  props=$(donotmix propose --json 2>/dev/null | python3 -c '
import json,sys
try: ps=json.load(sys.stdin)
except Exception: ps=[]
print(json.dumps([{"id":p.get("id"),"changes":{c["flag"]:c["to"] for c in (p.get("changes") or [])},"resolves":(p.get("evidence") or {}).get("resolves"),"still_failing":(p.get("evidence") or {}).get("still_failing"),"refused":p.get("refused")} for p in ps],separators=(",",":")))')
fi
echo "{\"exit\":$code,\"scan\":${brief:-null},\"proposals\":$props,\"approval\":\"human must run: donotmix approve <id> (TTY) or use the dashboard\"}"
exit $code
