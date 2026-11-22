"""Tool registry. LOW tools run immediately under the agent's identity. HIGH tools can only be *proposed* by an agent;
a human with `human_perm` must approve, and then the action runs under the human's identity.
Deliberately absent (never available to agents): sending messages, submitting applications, reading documents, deleting data."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from ..db import (Organization, ResearchResult, ResearchSource, ResearchRun, Application, Applicant, Document, Case, Task,
                  FraudReport, OutreachDraft, User)
from ..engine import verification, fraud, research
from .. import prep, notify, workflows

POSITIVE = ("visa", "sponsor", "scholarship", "recruit", "vacanc", "skilled", "work permit", "employer", "intake", "fellowship")
ORG_STATUSES = {"VERIFIED", "PARTIALLY VERIFIED", "UNVERIFIED", "SUSPICIOUS", "DISQUALIFIED", "INACTIVE", "EXPIRED"}


@dataclass
class Tool:
    fn: Callable
    risk: str                 # LOW | HIGH
    human_perm: str | None = None   # HIGH only
    keys: tuple = ()          # allowed argument names (anything else is rejected)


# ---------- LOW-risk tools ----------
def evaluate_org(s, actor, org_id):
    o = s.get(Organization, org_id)
    if not o:
        raise ValueError("organization not found")
    out = verification.evaluate(o, o.details.get("flags", []))
    have = {e["type"] for e in o.evidence}
    out["missing_authoritative"] = sorted(verification.AUTHORITATIVE - have)
    out["missing_supporting"] = sorted(verification.SUPPORTING - have)
    out["current_status"] = o.status
    return out


def triage_score(s, actor, result_id):
    r = s.get(ResearchResult, result_id)
    if not r:
        raise ValueError("lead not found")
    text = f"{r.title} {r.excerpt}".lower()          # lead text is DATA: counted, never interpreted as instructions
    score = sum(1 for w in POSITIVE if w in text)
    dup = s.query(ResearchResult).filter(ResearchResult.title == r.title, ResearchResult.id != r.id,
                                         ResearchResult.status != "REJECTED").count() > 0
    return {"result_id": r.id, "score": score, "source_level": r.source_level, "possible_duplicate": dup}


def quality_check_app(s, actor, application_id):
    a = s.get(Application, application_id)
    if not a:
        raise ValueError("application not found")
    docs = s.query(Document).filter_by(applicant_id=a.applicant_id, deleted=False).all()  # metadata only
    from ..db import Pathway
    return prep.quality_check(a, s.get(Pathway, a.pathway_id), s.get(Applicant, a.applicant_id).profile, docs)


def create_case_task(s, actor, applicant_id, title, due_in_days=3):
    c = s.query(Case).filter_by(applicant_id=applicant_id).first()
    if not c:
        raise ValueError("no case")
    if s.query(Task).filter_by(case_id=c.id, title=title, done=False).first():
        return {"created": False, "reason": "duplicate open task"}
    s.add(Task(case_id=c.id, title=title, assignee_id=c.assigned_to, due=(datetime.utcnow() + timedelta(days=int(due_in_days))).date()))
    s.commit()
    return {"created": True}


def scan_notifications(s, actor):
    return {"sent": notify.scan(s)}


def tick_workflows(s, actor):
    return {"resumed": workflows.tick(s)}


def list_stale_sources(s, actor):
    now = datetime.utcnow()
    return [{"id": x.id, "url": x.url} for x in s.query(ResearchSource).filter_by(active=True)
            if x.last_ok is None or (now - x.last_ok).total_seconds() > 2 * x.interval_hours * 3600]


def failed_runs(s, actor):
    return {"failed": s.query(ResearchRun).filter_by(status="FAILED").count(), "dead": s.query(ResearchRun).filter_by(status="DEAD").count()}


def scan_report(s, actor, report_id):
    r = s.get(FraudReport, report_id)
    if not r:
        raise ValueError("report not found")
    return {"report_id": r.id, "indicators": [h["indicator"] for h in fraud.scan_text(r.description)], "org_id": r.org_id}


def start_investigation(s, actor, report_id):
    r = s.get(FraudReport, report_id)
    if not r:
        raise ValueError("report not found")
    if r.status == "NEW":
        r.status = "INVESTIGATING"
        r.notes = [*r.notes, {"by": f"agent:{actor.email}", "at": datetime.utcnow().isoformat(timespec="seconds"), "status": "INVESTIGATING", "note": "triaged by agent"}]
        s.commit()
    return {"status": r.status}


def draft_outreach(s, actor, org_id, origin="Liberia"):
    o = s.get(Organization, org_id)
    if not o or o.status not in ("VERIFIED", "PARTIALLY VERIFIED"):
        raise ValueError("drafts are only prepared for verified or partially verified organizations")
    body = (f"Dear {o.name} team,\n\nI am writing on behalf of a migration advisory service to ask about your recruitment of international candidates. "
            f"Could you please confirm:\n1. Whether you sponsor work visas for overseas hires, and for which roles.\n"
            f"2. Whether you consider applicants from {origin}.\n3. Whether any fees are charged to applicants (we understand legitimate employers do not charge recruitment fees to workers).\n"
            f"4. Whether relocation or accommodation assistance is offered.\n5. The official application link and current deadlines.\n\n"
            f"Thank you for your time.\n\n[Draft for human review. Not sent.]")
    d = OutreachDraft(org_id=o.id, subject=f"Enquiry about international recruitment at {o.name}", body=body, created_by=actor.email)
    s.add(d); s.commit()
    return {"draft_id": d.id}


def run_research_source(s, actor, source_id):
    return {"run_id": research.execute(source_id)}


# ---------- HIGH-risk tools: executed only after human approval ----------
def apply_org_verification(s, actor, org_id, status, confidence=0, flags=()):
    if status not in ORG_STATUSES:
        raise ValueError("bad status")
    o = s.get(Organization, org_id)
    o.status, o.confidence, o.last_verified, o.needs_human_review = status, int(confidence), date.today(), False
    o.evidence = [*o.evidence, {"type": "human_review", "note": f"{status} applied by {actor.email} on an agent proposal; flags={list(flags)}", "date": date.today().isoformat()}]
    s.commit()
    return {"org_id": o.id, "status": o.status}


def review_lead(s, actor, result_id, decision):
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("bad decision")
    r = s.get(ResearchResult, result_id)
    r.status, r.reviewer = decision, actor.email
    s.commit()
    return {"result_id": r.id, "status": r.status}


def fraud_decision(s, actor, report_id, status, note=""):
    if status not in ("CONFIRMED", "DISMISSED"):
        raise ValueError("bad status")
    if status == "CONFIRMED" and not note.strip():
        raise ValueError("confirmation requires an evidence note")
    r = s.get(FraudReport, report_id)
    r.status = status
    r.notes = [*r.notes, {"by": actor.email, "at": datetime.utcnow().isoformat(timespec="seconds"), "status": status, "note": note}]
    if status == "CONFIRMED" and r.org_id and (o := s.get(Organization, r.org_id)):
        o.status, o.needs_human_review = "DISQUALIFIED", False
        o.evidence = [*o.evidence, {"type": "human_review", "note": f"fraud report {report_id} confirmed: {note}", "date": date.today().isoformat()}]
    s.commit()
    return {"report_id": r.id, "status": r.status}


TOOLS: dict[str, Tool] = {
    "evaluate_org": Tool(evaluate_org, "LOW", keys=("org_id",)),
    "triage_score": Tool(triage_score, "LOW", keys=("result_id",)),
    "quality_check_app": Tool(quality_check_app, "LOW", keys=("application_id",)),
    "create_case_task": Tool(create_case_task, "LOW", keys=("applicant_id", "title", "due_in_days")),
    "scan_notifications": Tool(scan_notifications, "LOW"),
    "tick_workflows": Tool(tick_workflows, "LOW"),
    "list_stale_sources": Tool(list_stale_sources, "LOW"),
    "failed_runs": Tool(failed_runs, "LOW"),
    "scan_report": Tool(scan_report, "LOW", keys=("report_id",)),
    "start_investigation": Tool(start_investigation, "LOW", keys=("report_id",)),
    "draft_outreach": Tool(draft_outreach, "LOW", keys=("org_id", "origin")),
    "run_research_source": Tool(run_research_source, "LOW", keys=("source_id",)),
    "apply_org_verification": Tool(apply_org_verification, "HIGH", "org.verify", ("org_id", "status", "confidence", "flags")),
    "review_lead": Tool(review_lead, "HIGH", "research.review", ("result_id", "decision")),
    "fraud_decision": Tool(fraud_decision, "HIGH", "fraud.investigate", ("report_id", "status", "note")),
}
