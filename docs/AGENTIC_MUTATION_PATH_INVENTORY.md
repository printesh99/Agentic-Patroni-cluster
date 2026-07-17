# Agentic Mutation Path Inventory

All agentic execution is disabled by default. `SHADOW` and `ADVISORY` never mutate. Enabling the legacy `PGC_ALLOW_MUTATIONS` or payload confirmation alone is insufficient; the central agentic policy must also allow execution, which this tranche does not configure in production.

| Path | Route/service | Potential mutation | Current guard |
|---|---|---|---|
| AI action audit | `api_ai_actions.py` → `approval_service.execute_action` | Records/dispatches approved actions | Trusted principal; self-approval rejected; central execution policy blocks |
| AI recommendations | `api_ai_agent.py`, `api_ai_v1.py` → `ai_agent_service.execute_recommendation` | Whitelisted SQL | Trusted principal; approval state; executor central policy blocks |
| SQL executor | `ai_agent_executor.execute_sql` | cancel/analyze/index DDL | Central policy checked before SQL classification or execution |
| Lifecycle | `api_actions.py` → `jobs.submit` | PGO replica/upgrade/decommission changes | `jobs.submit` central policy blocks before legacy mutation flag |
| Generic jobs | `api_actions.py` → `jobs.submit` | Callable executor | Central policy blocks; missing roles default to none |
| Cutover API | `api_actions.py`, `cutover/routes.py` | Patroni cutover | Central jobs policy for API path; vendored compatibility runner requires later centralization |
| Job registry | `jobs.py` | Invokes supplied executor | Central agentic policy, legacy mutation flag, RBAC |
| Cutover runner | `cutover/runner.py`, `cutover/wrapper.py` | Shell/Patroni/OpenShift operations | Compatibility path remains disabled; API jobs enter the typed central control policy and L4/L5 are categorically denied |

Every executable API job now passes `action_control_service`, which requires a typed executor, a canonical non-expired action plan, normalized subject-distinct approvals, CONTROLLED policy flags, current SHADOW validation evidence and—at L3—current backup/recovery evidence. Only allowlisted L3 actions (`analyze`, `cancel_backend`, `refresh_metadata`) can pass. L4/L5 remain prohibited.

No service account or OpenShift mutation permission is required for the disabled/shadow foundation. Read-only collectors retain their existing access requirements.
