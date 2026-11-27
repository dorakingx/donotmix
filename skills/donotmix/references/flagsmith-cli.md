# Flagsmith CLI essentials for DoNotMix

Install: `brew install Flagsmith/tap/flagsmith` or `curl -fsSL https://get.flagsmith.com | sh` (binary `flagsmith`, v2).

Credentials (non-interactive):
- Flagsmith Cloud: `FLAGSMITH_API_KEY=<Admin API key>` or `flagsmith login`.
- Self-hosted: keys are host-scoped. `http://localhost:8000` reads `FLAGSMITH_API_KEY_localhost_8000`; also set `FLAGSMITH_API_URL`.
- Check: `flagsmith auth status --jq .kind`.

Read-only commands DoNotMix uses (always add `--json` or `--jq`):
| purpose | command |
|---|---|
| resolve env | `flagsmith environment get Staging --jq .api_key` |
| flag states | `flagsmith flag list -e Staging --jq '[.[]|{f:.feature,on:.enabled}]'` |
| local-eval document | `flagsmith environment document Staging` |
| segments | `flagsmith segment list --jq '[.[]|.name]'` |
| server evaluation | `flagsmith evaluate -e Staging --identity u1 --trait beta=true --jq '.flags|map_values(.enabled)'` |

Writes (`flag enable|disable|update`) are done only by `donotmix apply` after human approval; the CLI itself also demands `--yes` without a TTY.
