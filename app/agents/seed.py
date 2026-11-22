from ..db import AgentConfig, User
from ..security import AGENT_KEYS

DEFS = {
    "research_triage": ("Research Triage Agent", "Scores and de-duplicates discovered research leads and proposes approve/reject for human review.",
                        ["triage_score", "review_lead"]),
    "verification_analyst": ("Verification Analyst Agent", "Evaluates organization evidence, lists missing authoritative evidence, and proposes a verification status for a human to apply.",
                             ["evaluate_org", "apply_org_verification"]),
    "case_prep": ("Case Preparation Agent", "Runs completeness checks on open applications and creates follow-up tasks for missing or expired documents.",
                  ["quality_check_app", "create_case_task"]),
    "fraud_triage": ("Compliance & Fraud Triage Agent", "Scans new fraud reports for indicators, starts investigations, and proposes outcomes with evidence for a compliance reviewer.",
                     ["scan_report", "start_investigation", "fraud_decision"]),
    "monitoring": ("Monitoring Agent", "Runs deadline/document scans and workflow timers, and reports stale sources and failed research runs.",
                   ["scan_notifications", "tick_workflows", "list_stale_sources", "failed_runs"]),
    "outreach": ("Outreach Drafting Agent", "Prepares enquiry email drafts for verified organizations. Drafts are never sent automatically.",
                 ["draft_outreach"]),
}


def seed_agents(s):
    for key in AGENT_KEYS:
        if s.get(AgentConfig, key):
            continue
        name, resp, tools = DEFS[key]
        u = User(email=f"agent-{key}@agents.invalid", pw_hash="!", role=f"agent_{key}")  # '!' can never verify: no login
        s.add(u); s.flush()
        s.add(AgentConfig(key=key, name=name, responsibilities=resp, allowed_tools=tools, user_id=u.id))
    s.commit()
