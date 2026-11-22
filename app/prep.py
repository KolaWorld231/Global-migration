"""Application preparation: checklist generation and quality assurance.
These are COMPLETENESS checks only. They do not authenticate documents; only the authority/issuer can."""
from datetime import date, timedelta

KEYWORDS = {"passport": "passport", "cv": "cv", "certificate": "certificate", "transcript": "transcript",
            "reference": "reference", "police": "police_clearance", "medical": "medical", "health": "medical",
            "language": "language_test", "offer": "offer_letter", "job offer": "offer_letter",
            "licence": "licence", "credential": "certificate", "letter of acceptance": "offer_letter",
            "admission": "offer_letter", "proof of funds": "other"}
STAGES = ["PREPARING", "READY", "SUBMITTED", "ACKNOWLEDGED", "INTERVIEW", "DOCUMENTS_REQUESTED",
          "ASSESSMENT", "DECISION", "SUCCESSFUL", "UNSUCCESSFUL", "WITHDRAWN"]
ALLOWED_NEXT = {
    "PREPARING": {"READY", "WITHDRAWN"}, "READY": {"PREPARING", "SUBMITTED", "WITHDRAWN"},
    "SUBMITTED": {"ACKNOWLEDGED", "INTERVIEW", "DOCUMENTS_REQUESTED", "ASSESSMENT", "DECISION", "WITHDRAWN"},
    "ACKNOWLEDGED": {"INTERVIEW", "DOCUMENTS_REQUESTED", "ASSESSMENT", "DECISION", "WITHDRAWN"},
    "INTERVIEW": {"DOCUMENTS_REQUESTED", "ASSESSMENT", "DECISION", "WITHDRAWN"},
    "DOCUMENTS_REQUESTED": {"ASSESSMENT", "INTERVIEW", "DECISION", "WITHDRAWN"},
    "ASSESSMENT": {"INTERVIEW", "DOCUMENTS_REQUESTED", "DECISION", "WITHDRAWN"},
    "DECISION": {"SUCCESSFUL", "UNSUCCESSFUL"}, "SUCCESSFUL": set(), "UNSUCCESSFUL": set(), "WITHDRAWN": set(),
}


def doc_type_for(label: str) -> str:
    l = label.lower()
    for k in sorted(KEYWORDS, key=len, reverse=True):
        if k in l:
            return KEYWORDS[k]
    return "other"


def checklist(pathway, docs, today=None):
    today = today or date.today()
    held = {}
    for d in docs:
        if d.deleted:
            continue
        expired = bool(d.expires_on and d.expires_on < today)
        held.setdefault(d.doc_type, []).append((d, expired))
    out = []
    for label in pathway.documents:
        t = doc_type_for(label)
        have = held.get(t, [])
        valid = [x for x in have if not x[1]]
        status = "PROVIDED" if valid else ("EXPIRED" if have else "MISSING")
        if t == "other":
            status = "APPLICANT_TO_CONFIRM"
        out.append({"requirement": label, "doc_type": t, "status": status})
    return out


def quality_check(app, pathway, profile, docs, today=None):
    today = today or date.today()
    issues = []
    add = lambda sev, msg: issues.append({"severity": sev, "message": msg})
    for c in checklist(pathway, docs, today):
        if c["status"] == "MISSING":
            add("blocker", f"Missing document: {c['requirement']}")
        elif c["status"] == "EXPIRED":
            add("blocker", f"Document expired: {c['requirement']}")
    for d in docs:
        if d.deleted:
            continue
        if d.scan_status != "CLEAN":
            add("info", f"'{d.filename}' has not been malware-scanned (scan status {d.scan_status}).")
        if d.doc_type == "passport" and d.expires_on and today <= d.expires_on < today + timedelta(days=183):
            add("warning", "Passport expires within 6 months; check the authority's passport validity rule.")
    if app.deadline and app.deadline < today:
        add("blocker", "Application deadline has passed.")
    elif app.deadline and (app.deadline - today).days <= 14:
        add("warning", f"Deadline in {(app.deadline - today).days} days.")
    for j in profile.get("employment", {}).get("history", []) or []:
        s, e = j.get("start"), j.get("end")
        if s and e and e < s:
            add("blocker", f"Employment dates inconsistent at {j.get('employer', 'an employer')}: end before start.")
    for key, label in (("full_name", "full legal name"), ("dob", "date of birth"), ("nationality", "nationality")):
        if not profile.get(key):
            add("blocker", f"Profile is missing {label}.")
    if not app.authorized:
        add("blocker", "Applicant has not authorised preparation to proceed.")
    return {"ready": not any(i["severity"] == "blocker" for i in issues),
            "issues": issues,
            "disclaimer": "Completeness check only. This does not verify that any document is genuine; claims must be "
                          "supported by your own evidence. The platform never submits applications or signs declarations for you."}
