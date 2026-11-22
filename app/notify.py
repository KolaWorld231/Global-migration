"""Notification service. Critical (application/deadline/document/regulatory) vs non-essential (opportunity alerts, marketing).
Non-essential messages are sent ONLY to users who opted in. Email goes through an adapter; with no provider configured
the notification is stored in-app and email_status says NOT_CONFIGURED (never silently 'sent')."""
import os
from datetime import date, timedelta
from .db import Notification, Applicant, User, Application, Document, Pathway, Setting

CRITICAL_KINDS = {"deadline", "document_expiry", "regulatory_change", "application_action", "interview", "verification_change", "account"}
DEFAULT_PREFS = {"email": True, "marketing": False, "opportunity_alerts": False}


def prefs(s, user_id):
    r = s.get(Setting, f"prefs:{user_id}")
    return {**DEFAULT_PREFS, **(r.value if r else {})}


def set_prefs(s, user_id, new):
    clean = {k: bool(v) for k, v in new.items() if k in DEFAULT_PREFS}
    s.merge(Setting(key=f"prefs:{user_id}", value={**prefs(s, user_id), **clean})); s.commit()
    return prefs(s, user_id)


class EmailAdapter:
    """Interface. Subclass for Resend/SMTP/etc. The default sends nothing."""
    def send(self, to, subject, body) -> str:
        return "NOT_CONFIGURED"


EMAIL = EmailAdapter()


def notify(s, user_id, kind, title, body="", key=""):
    """Returns Notification or None (suppressed by prefs, or duplicate)."""
    critical = kind in CRITICAL_KINDS
    p = prefs(s, user_id)
    if not critical and not (p["opportunity_alerts"] if kind == "opportunity_match" else p["marketing"]):
        return None
    dk = f"{kind}:{key or title}"
    if s.query(Notification).filter_by(user_id=user_id, dedupe_key=dk).first():
        return None
    n = Notification(user_id=user_id, kind=kind, dedupe_key=dk, title=title, body=body, critical=critical)
    if p["email"]:
        u = s.get(User, user_id)
        n.email_status = EMAIL.send(u.email, title, body) if u else "NOT_CONFIGURED"
    s.add(n); s.commit()
    return n


def scan(s, today=None):
    """Deadline and document-expiry monitoring. Idempotent thanks to dedupe keys."""
    today = today or date.today()
    sent = 0
    users = {u.applicant_id: u.id for u in s.query(User).filter(User.applicant_id.isnot(None))}
    for a in s.query(Application).filter(Application.status.in_(["PREPARING", "READY"])):
        if a.deadline and today <= a.deadline <= today + timedelta(days=14) and a.applicant_id in users:
            d = (a.deadline - today).days
            sent += bool(notify(s, users[a.applicant_id], "deadline", f"Application deadline in {d} day(s)",
                                f"Your application for {a.pathway_id} is due {a.deadline}. Confirm the date on the official site.",
                                key=f"app{a.id}:{a.deadline}:{min(d, 7) if d <= 7 else 14}"))
    for d in s.query(Document).filter_by(deleted=False):
        if d.expires_on and d.expires_on <= today + timedelta(days=30) and d.applicant_id in users:
            state = "has expired" if d.expires_on < today else f"expires on {d.expires_on}"
            sent += bool(notify(s, users[d.applicant_id], "document_expiry", f"Your {d.doc_type} {state}",
                                "Renew it before applying; many authorities reject expired documents.", key=f"doc{d.id}:{state}"))
    return sent


def notify_country_change(s, country):
    """After a reviewer confirms a regulatory impact: tell applicants who target or applied to that country."""
    pw_ids = [p.id for p in s.query(Pathway).filter_by(country=country)]
    target = set(a.applicant_id for a in s.query(Application).filter(Application.pathway_id.in_(pw_ids)))
    for ap in s.query(Applicant):
        dests = [x.upper() for x in ((ap.profile.get("preferences") or {}).get("destinations") or [])]
        if country in dests:
            target.add(ap.id)
    n = 0
    for u in s.query(User).filter(User.applicant_id.in_(target or {-1})):
        n += bool(notify(s, u.id, "regulatory_change", f"Immigration rules may have changed for {country}",
                         "A reviewer confirmed a change on an official source. Re-check requirements before applying; "
                         "your assessment may be out of date.", key=f"{country}:{date.today()}"))
    return n
