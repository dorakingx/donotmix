# Change safety protocol

1. `donotmix propose` creates proposals only for live hazards in non-protected environments. Each candidate state is executed (all personas) before it is proposed; evidence shows passes, resolved hazards and anything still failing.
2. Approval is human-only: `donotmix approve <id>` refuses without an interactive terminal; the dashboard requires typing the environment name and an approver name. Approval records the proposal's content hash in `.donotmix/audit.jsonl` (hash chain).
3. `donotmix apply <id>` refuses if: no approval, proposal edited after approval, environment protected, already applied, or live flag states drifted from the proposal's preconditions. It writes only the approved flags via `flagsmith flag enable|disable`, reads each back, logs it, then re-scans.
4. `donotmix rollback <id>` creates an inverse proposal that goes through the same approval.
5. `donotmix audit` verifies the hash chain (exit 6 if tampered).
