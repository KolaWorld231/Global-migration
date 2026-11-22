# AI system guide: agents as staff

## Model
Agents are staff identities (`User` with role `agent_<key>`, password `!` so they cannot log in). Humans assign tasks or workflows queue them;
agents execute and **propose**, humans approve anything high-risk. Every agent action is audited under the agent's own user id.

## Agents
| Key | Responsibility | Tools (LOW = runs, HIGH = proposal only) |
|---|---|---|
| research_triage | Score/dedupe research leads, propose approve/reject | triage_score (LOW), review_lead (HIGH, needs `research.review`) |
| verification_analyst | Evaluate org evidence, list gaps, propose status | evaluate_org (LOW), apply_org_verification (HIGH, `org.verify`) |
| case_prep | Completeness checks, follow-up tasks | quality_check_app, create_case_task (LOW) |
| fraud_triage | Scan reports, start investigation, propose outcome | scan_report, start_investigation (LOW), fraud_decision (HIGH, `fraud.investigate`) |
| monitoring | Deadline/doc scans, workflow timers, stale/failed source report | scan_notifications, tick_workflows, list_stale_sources, failed_runs (LOW) |
| outreach | Enquiry email drafts for verified orgs (never sent) | draft_outreach (LOW) |

## Hard limits (enforced in code, tested)
- A tool must be in the agent's allowlist; arguments are restricted to declared keys; per-run step budget; per-agent and global pause.
- HIGH tools only create an `approval_request`; execution happens under the approving human's identity and permission.
- No tool exists for sending messages, submitting applications, signing, reading documents or deleting data.
- Lead/page text is data (counted/scored), never executed or interpreted as instructions.

## Models
`app/agents/providers.py`: `LLMProvider` interface, `NullProvider` (default; deterministic handlers), `MockProvider` (tests). Register a real adapter in `PROVIDERS`
and set an agent's `model_provider`; its tool requests pass the same validation. **No real model is wired or tested.**

## Operating
Staff tab > AI agents (run, pause, pause-all), approvals queue. API: `/api/admin/agents*`, `/api/admin/approvals*`. Workflows can use the `agent_task` step.
