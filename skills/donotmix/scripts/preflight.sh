#!/usr/bin/env bash
# One-line JSON preflight for the donotmix skill. Usage: preflight.sh [ENVIRONMENT]
set -uo pipefail
ENV_NAME="${1:-}"
j() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
cli=false; ver=""; auth=false; authd=""; env_ok=null; envd=""; cfg=false; dnm=false
if command -v flagsmith >/dev/null 2>&1; then cli=true; ver=$(flagsmith --version 2>/dev/null | awk '{print $NF}'); fi
if command -v donotmix >/dev/null 2>&1; then dnm=true; fi
[ -f donotmix.yaml ] && cfg=true
if $cli; then
  if out=$(flagsmith --no-input auth status --jq '"\(.kind) \(.apiUrl)"' 2>&1); then auth=true; authd=$(echo "$out" | tr -d '"'); else authd=$(echo "$out" | head -1); fi
  if [ -z "$ENV_NAME" ] && $cfg; then ENV_NAME=$(python3 -c "import yaml;print(yaml.safe_load(open('donotmix.yaml')).get('environment') or '')" 2>/dev/null); fi
  if $auth && [ -n "$ENV_NAME" ]; then
    proj=""; $cfg && proj=$(python3 -c "import yaml;print(yaml.safe_load(open('donotmix.yaml')).get('project') or '')" 2>/dev/null)
    if out=$(flagsmith --no-input environment get "$ENV_NAME" ${proj:+--project "$proj"} --jq '"\(.name) \(.api_key)"' 2>&1); then env_ok=true; envd=$(echo "$out" | tr -d '"'); else env_ok=false; envd=$(echo "$out" | head -1); fi
  fi
fi
printf '{"flagsmith_cli":%s,"cli_version":%s,"auth":%s,"auth_detail":%s,"environment":%s,"environment_ok":%s,"environment_detail":%s,"donotmix":%s,"config":%s}\n' \
  "$cli" "$(j "$ver")" "$auth" "$(j "$authd")" "$(j "$ENV_NAME")" "$env_ok" "$(j "$envd")" "$dnm" "$cfg"
