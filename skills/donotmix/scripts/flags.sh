#!/usr/bin/env bash
# Compact flag state for one environment: [{f, on, seg}] (seg = number of segment overrides)
set -euo pipefail
ENV_NAME="${1:?usage: flags.sh ENVIRONMENT [PROJECT]}"
PROJ="${2:-}"
[ -z "$PROJ" ] && [ -f donotmix.yaml ] && PROJ=$(python3 -c "import yaml;print(yaml.safe_load(open('donotmix.yaml')).get('project') or '')" 2>/dev/null || true)
flagsmith --no-input flag list -e "$ENV_NAME" ${PROJ:+--project "$PROJ"} --jq '[.[] | {f: .feature, on: .enabled, seg: .segment_overrides}]'
