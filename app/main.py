import os, secrets
from datetime import date, datetime, timedelta
from typing import Any
from sqlalchemy import text
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .db import (init_db, SessionLocal, Country, Pathway, Organization, Opportunity, Applicant, Case, DiscoveryJob)
from .seed.loader import seed
from .engine import eligibility, verification, advisor, discovery, matching
from . import security, vault, prep, notify, workflows
from .agents import runtime as agent_rt, seed as agent_seed
from .db import AgentConfig, AgentTask, ApprovalRequest, OutreachDraft
from .db import Workflow, WorkflowRun, AuthSession
from .db import Notification, CaseNote, Task, Communication, Setting
from .engine import fraud, research
from .db import ResearchSource, ResearchRun, ResearchResult, RegulatoryChange, SourceSnapshot, FraudReport
from .security import require, current_user, audit
from .db import User, AuditLog, Document, Application
from fastapi import UploadFile, File, Form

app = FastAPI(title="Global Pathways", version="0.1.0")
@app.on_event("startup")
def _startup():
    init_db()
    seed()
    bootstrap_admin()
    with SessionLocal() as _s: workflows.seed_defaults(_s); agent_seed.seed_agents(_s)
    if os.environ.get("SCHEDULER") == "1":
        import threading, time
        def loop():
            while True:
                try:
                    research.run_due()
                    with SessionLocal() as s: notify.scan(s); workflows.tick(s); agent_rt.run_queued(s)
                except Exception as e: print('[scheduler]', e)
                time.sleep(900)
        threading.Thread(target=loop, daemon=True).start()


def db():
    with SessionLocal() as s:
        yield s


def row(o):
    return {c.name: (getattr(o, c.name).isoformat() if isinstance(getattr(o, c.name), date) else getattr(o, c.name))
            for c in o.__table__.columns}


# ---------- Country management (admin) ----------
class CountryIn(BaseModel):
    iso2: str; iso3: str; name: str
    is_destination: bool = False; is_origin: bool = False
    config: dict = {}


@app.get("/api/countries")
def countries(role: str | None = None, s=Depends(db)):
    q = s.query(Country)
    if role == "destination": q = q.filter_by(is_destination=True)
    if role == "origin": q = q.filter_by(is_origin=True)
    return [row(c) for c in q.all()]


@app.post("/api/admin/countries", dependencies=[Depends(require("catalog.manage"))])
def add_country(c: CountryIn, s=Depends(db)):
    iso = c.iso2.upper()
    if s.get(Country, iso): raise HTTPException(409, "country exists")
    s.add(Country(iso2=iso, iso3=c.iso3.upper(), name=c.name, is_destination=c.is_destination,
                  is_origin=c.is_origin, config=c.config, active=False))
    s.commit()
    return {"iso2": iso, "active": False, "next": "add pathways, then POST /api/admin/countries/{iso}/activate"}


@app.post("/api/admin/countries/{iso}/activate", dependencies=[Depends(require("catalog.manage"))])
def activate(iso: str, active: bool = True, s=Depends(db)):
    c = s.get(Country, iso.upper())
    if not c: raise HTTPException(404)
    if active and c.is_destination:
        if not c.config.get("authority") or not c.config.get("official_sites"):
            raise HTTPException(422, "destination needs authority and official_sites before activation")
        if not s.query(Pathway).filter_by(country=c.iso2).count():
            raise HTTPException(422, "add at least one pathway first")
    c.active = active; s.commit()
    return row(c)


@app.post("/api/admin/countries/{iso}/verify", dependencies=[Depends(require("catalog.verify"))])
def verify_country(iso: str, reliability: str = "official", s=Depends(db)):
    c = s.get(Country, iso.upper()); c.last_verified = date.today(); c.source_reliability = reliability; s.commit()
    return row(c)


class PathwayIn(BaseModel):
    id: str; country: str; name: str; category: str
    official_url: str = ""; requirements: list[dict] = []; documents: list[str] = []
    fees: dict = {}; excluded_nationalities: list[str] = []; allowed_nationalities: list[str] = []


@app.post("/api/admin/pathways", dependencies=[Depends(require("catalog.manage"))])
def upsert_pathway(p: PathwayIn, s=Depends(db)):
    if not s.get(Country, p.country.upper()): raise HTTPException(404, "unknown country")
    s.merge(Pathway(**{**p.model_dump(), "country": p.country.upper()})); s.commit()
    return {"ok": True}


@app.post("/api/admin/pathways/{pid}/verify", dependencies=[Depends(require("catalog.verify"))])
def verify_pathway(pid: str, s=Depends(db)):
    """A human attests the requirements were checked against the official source today."""
    p = s.get(Pathway, pid)
    if not p: raise HTTPException(404)
    p.last_verified = date.today(); s.commit()
    return row(p)


@app.get("/api/pathways")
def pathways(country: str | None = None, s=Depends(db)):
    q = s.query(Pathway)
    if country: q = q.filter_by(country=country.upper())
    return [row(p) for p in q.all()]


# ---------- Organizations & verification ----------
class OrgIn(BaseModel):
    name: str; category: str; country: str
    origins_accepted: list[str] = []; website: str = ""; details: dict = {}
    source_urls: list[str] = []; evidence: list[dict] = []; notes: str = ""


@app.post("/api/admin/organizations", dependencies=[Depends(require("org.manage"))])
def add_org(o: OrgIn, s=Depends(db)):
    org = Organization(**o.model_dump()); s.add(org); s.commit()
    return row(org)


@app.get("/api/organizations")
def orgs(country: str | None = None, status: str | None = None, category: str | None = None, s=Depends(db)):
    q = s.query(Organization)
    if country: q = q.filter_by(country=country.upper())
    if status: q = q.filter_by(status=status)
    if category: q = q.filter_by(category=category)
    return [row(o) for o in q.all()]


class EvidenceIn(BaseModel):
    type: str; url: str = ""; note: str = ""
    flags: list[str] = []


@app.post("/api/admin/organizations/{oid}/evidence", dependencies=[Depends(require("org.verify"))])
def add_evidence(oid: int, e: EvidenceIn, s=Depends(db)):
    o = s.get(Organization, oid)
    if not o: raise HTTPException(404)
    if e.type in verification.WEIGHTS or e.type == "note":
        o.evidence = [*o.evidence, {"type": e.type, "url": e.url, "note": e.note, "date": date.today().isoformat()}]
    flags = set(o.details.get("flags", [])) | set(e.flags)
    o.details = {**o.details, "flags": sorted(flags)}
    s.commit()
    return row(o)


@app.post("/api/admin/organizations/{oid}/evaluate", dependencies=[Depends(require("org.verify"))])
def evaluate_org(oid: int, s=Depends(db)):
    o = s.get(Organization, oid)
    if not o: raise HTTPException(404)
    out = verification.evaluate(o, o.details.get("flags", []))
    verification.apply(o, out); s.commit()
    return {"organization": row(o), "outcome": out}


@app.post("/api/admin/organizations/{oid}/decision", dependencies=[Depends(require("org.verify"))])
def human_decision(oid: int, status: str, reason: str, s=Depends(db)):
    if status not in {"VERIFIED", "PARTIALLY VERIFIED", "UNVERIFIED", "SUSPICIOUS", "DISQUALIFIED", "INACTIVE", "EXPIRED"}:
        raise HTTPException(422, "bad status")
    o = s.get(Organization, oid)
    o.status = status; o.needs_human_review = False; o.last_verified = date.today()
    o.evidence = [*o.evidence, {"type": "human_review", "note": f"{status}: {reason}", "date": date.today().isoformat()}]
    s.commit()
    return row(o)


# ---------- Opportunities ----------
class OppIn(BaseModel):
    title: str; org_id: int; country: str; kind: str
    pathway_id: str | None = None; occupation: str = ""; industry: str = ""
    min_education: str = "none"; min_experience_years: float = 0
    sponsorship: bool = False; relocation: bool = False; accommodation: bool = False
    applicant_fee: bool | None = None; salary_min: float | None = None; salary_currency: str = ""
    nationality_allowed: list[str] = []; nationality_excluded: list[str] = []
    language_required: dict = {}; deadline: date | None = None; url: str = ""; details: dict = {}


@app.post("/api/admin/opportunities", dependencies=[Depends(require("opp.manage"))])
def add_opp(o: OppIn, s=Depends(db)):
    org = s.get(Organization, o.org_id)
    if not org: raise HTTPException(404, "organization not found")
    opp = Opportunity(**{**o.model_dump(), "country": o.country.upper()}, last_checked=date.today())
    # An opportunity can never be more verified than its organization.
    opp.verification = org.status if org.status in ("VERIFIED", "PARTIALLY VERIFIED", "SUSPICIOUS", "DISQUALIFIED") else "UNVERIFIED"
    s.add(opp); s.commit()
    return row(opp)


@app.get("/api/opportunities")
def search_opps(country: str | None = None, nationality: str | None = None, kind: str | None = None,
                occupation: str | None = None, industry: str | None = None, sponsorship: bool | None = None,
                relocation: bool | None = None, accommodation: bool | None = None, no_applicant_fee: bool = False,
                min_salary: float | None = None, deadline_after: date | None = None,
                verification_status: str | None = None, include_unverified: bool = False,
                education: str | None = None, experience_years: float | None = None, s=Depends(db)):
    q = s.query(Opportunity)
    today = date.today()
    if country: q = q.filter_by(country=country.upper())
    if kind: q = q.filter_by(kind=kind)
    if occupation: q = q.filter(Opportunity.occupation.ilike(f"%{occupation}%"))
    if industry: q = q.filter(Opportunity.industry.ilike(f"%{industry}%"))
    if sponsorship is not None: q = q.filter_by(sponsorship=sponsorship)
    if relocation is not None: q = q.filter_by(relocation=relocation)
    if accommodation is not None: q = q.filter_by(accommodation=accommodation)
    if no_applicant_fee: q = q.filter(Opportunity.applicant_fee.is_(False))
    if min_salary is not None: q = q.filter(Opportunity.salary_min >= min_salary)
    if verification_status:
        q = q.filter_by(verification=verification_status)
    elif not include_unverified:
        q = q.filter(Opportunity.verification.in_(["VERIFIED", "PARTIALLY VERIFIED"]))
    res = []
    for o in q.all():
        if deadline_after and (o.deadline is None or o.deadline < deadline_after): continue
        r = row(o)
        r["expired"] = bool(o.deadline and o.deadline < today)
        if r["expired"] or o.status != "OPEN":
            if not verification_status: continue
        if nationality:
            n = nationality.upper()
            if n in [x.upper() for x in o.nationality_excluded]: continue
            if o.nationality_allowed and n not in [x.upper() for x in o.nationality_allowed]: continue
        if education and education in eligibility.EDU_ORDER and eligibility.EDU_ORDER.index(education) < eligibility.EDU_ORDER.index(o.min_education): continue
        if experience_years is not None and experience_years < o.min_experience_years: continue
        res.append(r)
    return res


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: str; password: str


def bootstrap_admin():
    em, pw = os.environ.get("BOOTSTRAP_ADMIN_EMAIL"), os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not (em and pw): return
    with SessionLocal() as s:
        if not s.query(User).filter_by(email=em.lower()).first():
            s.add(User(email=em.lower(), pw_hash=security.hash_pw(pw), role="super_admin")); s.commit()


@app.post("/api/auth/register")
def register(r: RegisterIn, s=Depends(db)):
    security.valid_password(r.password)
    email = r.email.strip().lower()
    if "@" not in email or s.query(User).filter_by(email=email).first():
        raise HTTPException(409, "email invalid or already registered")
    ap = Applicant(profile={}, consent={}); s.add(ap); s.flush()
    u = User(email=email, pw_hash=security.hash_pw(r.password), role="applicant", applicant_id=ap.id)
    s.add(u); s.add(Case(applicant_id=ap.id, history=[{"stage": "NEW_APPLICANT", "at": datetime.utcnow().isoformat(timespec="seconds"), "by": None}])); s.commit(); audit(s, u.id, "register")
    workflows.emit(s, "applicant.registered", {"applicant_id": ap.id, "user_id": u.id})
    return {"token": security.login(s, email, r.password), "applicant_id": ap.id}


@app.post("/api/auth/login")
def do_login(r: RegisterIn, s=Depends(db)):
    return {"token": security.login(s, r.email, r.password)}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default=""), s=Depends(db)):
    security.revoke(s, authorization[7:]); return {"ok": True}


@app.get("/api/auth/me")
def me(u: User = Depends(current_user)):
    return {"id": u.id, "email": u.email, "role": u.role, "applicant_id": u.applicant_id,
            "permissions": sorted(security.PERMS.get(u.role, []))}


class StaffIn(BaseModel):
    email: str; password: str; role: str


@app.post("/api/admin/staff")
def add_staff(b: StaffIn, u: User = Depends(require("user.manage")), s=Depends(db)):
    if b.role not in security.ROLES or b.role == "applicant" or b.role.startswith("agent_"): raise HTTPException(422, "bad role")
    security.valid_password(b.password)
    s.add(User(email=b.email.lower(), pw_hash=security.hash_pw(b.password), role=b.role)); s.commit()
    audit(s, u.id, "staff_created", b.email, role=b.role)
    return {"ok": True}


@app.get("/api/admin/audit")
def audit_log(limit: int = 100, u: User = Depends(require("audit.read")), s=Depends(db)):
    return [row(a) | {"ts": a.ts.isoformat()} for a in s.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))]


# ---------- Applicants, assessment, advisor ----------
def owned(aid: int, u: User, s, write=False):
    if u.applicant_id == aid or (not write and security.can_access_applicant(u, aid)):
        ap = s.get(Applicant, aid)
        if ap:
            if u.applicant_id != aid: audit(s, u.id, "applicant_viewed", aid)
            return ap
    raise HTTPException(404)  # 404, not 403: do not reveal existence


class ApplicantIn(BaseModel):
    profile: dict[str, Any] = {}
    consent: dict[str, str] = {}


@app.put("/api/applicants/{aid}")
def update_applicant(aid: int, a: ApplicantIn, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s, write=True)
    ap.profile = {**ap.profile, **a.profile}; ap.consent = {**ap.consent, **a.consent}; s.commit()
    audit(s, u.id, "profile_updated", aid, consent_keys=sorted(a.consent))
    return {"id": ap.id, "profile": ap.profile, "consent": ap.consent}


@app.get("/api/applicants/{aid}")
def get_applicant(aid: int, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s)
    return {"id": ap.id, "profile": ap.profile, "consent": ap.consent}


@app.delete("/api/applicants/{aid}")
def delete_applicant(aid: int, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s, write=True)
    for d in s.query(Document).filter_by(applicant_id=aid):
        vault.remove(d.storage_key); s.delete(d)
    c = s.query(Case).filter_by(applicant_id=aid).first()
    if c:
        for M in (CaseNote, Task, Communication): s.query(M).filter_by(case_id=c.id).delete()
        s.delete(c)
    s.query(Notification).filter(Notification.user_id.in_([x.id for x in s.query(User).filter_by(applicant_id=aid)] or [-1])).delete()
    s.query(Application).filter_by(applicant_id=aid).delete()
    s.query(User).filter_by(applicant_id=aid).delete(); s.delete(ap); s.commit()
    audit(s, None, "applicant_deleted", aid)
    return {"deleted": True}


@app.get("/api/applicants/{aid}/export")
def export_applicant(aid: int, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s, write=True)
    audit(s, u.id, "data_exported", aid)
    return {"profile": ap.profile, "consent": ap.consent,
            "documents": [row(d) for d in s.query(Document).filter_by(applicant_id=aid, deleted=False)],
            "applications": [row(x) for x in s.query(Application).filter_by(applicant_id=aid)]}


@app.get("/api/applicants/{aid}/assessment")
def assessment(aid: int, country: str | None = None, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s)
    pws = s.query(Pathway).join(Country, Country.iso2 == Pathway.country).filter(Country.active.is_(True)).all()
    return eligibility.assess_all(pws, ap.profile, country.upper() if country else None)


@app.get("/api/applicants/{aid}/matches")
def matches(aid: int, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s)
    return matching.match(s, ap.profile)


class AdviceIn(BaseModel):
    mode: str = "general"; country: str | None = None; pathway_id: str | None = None


@app.post("/api/applicants/{aid}/advisor")
def advise(aid: int, body: AdviceIn, u: User = Depends(current_user), s=Depends(db)):
    ap = owned(aid, u, s)
    if body.mode not in advisor.MODES: raise HTTPException(422, f"mode must be one of {advisor.MODES}")
    return advisor.answer(s, body.mode, ap.profile, ap.consent, country=body.country, pathway_id=body.pathway_id)


# ---------- Documents (private vault) ----------
def can_read_docs(u: User, aid: int, s) -> bool:
    if u.applicant_id == aid: return True
    ap = s.get(Applicant, aid)
    return "document.read" in security.PERMS.get(u.role, set()) and ap and ap.consent.get("documents_staff") == "share"


@app.post("/api/applicants/{aid}/documents")
async def upload_doc(aid: int, doc_type: str = Form(...), expires_on: date | None = Form(None),
                     file: UploadFile = File(...), u: User = Depends(current_user), s=Depends(db)):
    owned(aid, u, s, write=True)
    if doc_type not in vault.DOC_TYPES: raise HTTPException(422, f"doc_type must be one of {vault.DOC_TYPES}")
    data = await file.read(vault.MAX_BYTES + 1)
    try: ctype = vault.validate(file.filename or "", data)
    except ValueError as e: raise HTTPException(422, str(e))
    import hashlib
    d = Document(applicant_id=aid, doc_type=doc_type, filename=vault.safe_name(file.filename), content_type=ctype,
                 size=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_key=vault.put(data),
                 expires_on=expires_on, scan_status=vault.scan(data))
    s.add(d); s.commit(); audit(s, u.id, "document_uploaded", d.id, type=doc_type)
    return row(d) | {"note": "Stored encrypted. Scan status NOT_SCANNED means no malware scanner is configured."}


@app.get("/api/applicants/{aid}/documents")
def list_docs(aid: int, u: User = Depends(current_user), s=Depends(db)):
    owned(aid, u, s)  # metadata visible to staff with applicant.read; content is not
    return [row(d) for d in s.query(Document).filter_by(applicant_id=aid, deleted=False)]


@app.get("/api/documents/{did}/content")
def doc_content(did: int, u: User = Depends(current_user), s=Depends(db)):
    from fastapi.responses import Response
    d = s.get(Document, did)
    if not d or d.deleted or not can_read_docs(u, d.applicant_id, s):
        if d: audit(s, u.id, "document_denied", did)
        raise HTTPException(404)
    audit(s, u.id, "document_read", did)
    return Response(vault.get(d.storage_key), media_type=d.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{d.filename}"', "Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})


@app.delete("/api/documents/{did}")
def delete_doc(did: int, u: User = Depends(current_user), s=Depends(db)):
    d = s.get(Document, did)
    if not d or d.applicant_id != u.applicant_id: raise HTTPException(404)
    vault.remove(d.storage_key); d.deleted = True; s.commit(); audit(s, u.id, "document_deleted", did)
    return {"deleted": True}


# ---------- Application preparation ----------
class AppIn(BaseModel):
    pathway_id: str; opportunity_id: int | None = None; deadline: date | None = None


def my_app(aid: int, u: User, s) -> Application:
    a = s.get(Application, aid)
    if not a or a.applicant_id != u.applicant_id: raise HTTPException(404)
    return a


def log_event(a, kind, **kw):
    a.events = [*a.events, {"at": datetime.utcnow().isoformat(timespec="seconds"), "event": kind, **kw}]
    a.updated = datetime.utcnow()


@app.post("/api/applications")
def create_application(b: AppIn, u: User = Depends(current_user), s=Depends(db)):
    if u.applicant_id is None or not s.get(Pathway, b.pathway_id): raise HTTPException(404)
    dl = b.deadline
    if b.opportunity_id:
        o = s.get(Opportunity, b.opportunity_id)
        if not o: raise HTTPException(404, "opportunity not found")
        dl = dl or o.deadline
    a = Application(applicant_id=u.applicant_id, pathway_id=b.pathway_id, opportunity_id=b.opportunity_id, deadline=dl, events=[])
    log_event(a, "created"); s.add(a); s.commit()
    return row(a)


@app.get("/api/applications")
def my_applications(u: User = Depends(current_user), s=Depends(db)):
    return [row(a) for a in s.query(Application).filter_by(applicant_id=u.applicant_id)]


@app.get("/api/applications/{aid}/checklist")
def app_checklist(aid: int, u: User = Depends(current_user), s=Depends(db)):
    a = my_app(aid, u, s)
    docs = s.query(Document).filter_by(applicant_id=a.applicant_id, deleted=False).all()
    return {"checklist": prep.checklist(s.get(Pathway, a.pathway_id), docs),
            "official_url": s.get(Pathway, a.pathway_id).official_url}


@app.post("/api/applications/{aid}/authorize")
def authorize(aid: int, u: User = Depends(current_user), s=Depends(db)):
    a = my_app(aid, u, s); a.authorized = True; log_event(a, "applicant_authorized_preparation")
    s.commit(); audit(s, u.id, "application_authorized", aid); return row(a)


@app.get("/api/applications/{aid}/quality")
def app_quality(aid: int, u: User = Depends(current_user), s=Depends(db)):
    a = my_app(aid, u, s)
    docs = s.query(Document).filter_by(applicant_id=a.applicant_id, deleted=False).all()
    return prep.quality_check(a, s.get(Pathway, a.pathway_id), s.get(Applicant, a.applicant_id).profile, docs)


class StatusIn(BaseModel):
    status: str; reference: str = ""; follow_up_on: date | None = None; note: str = ""


@app.post("/api/applications/{aid}/status")
def set_status(aid: int, b: StatusIn, u: User = Depends(current_user), s=Depends(db)):
    """Applicants RECORD what happened on the official portal. The platform never submits on their behalf."""
    a = my_app(aid, u, s)
    if b.status not in prep.ALLOWED_NEXT.get(a.status, set()):
        raise HTTPException(422, f"cannot move from {a.status} to {b.status}")
    if b.status == "READY":
        docs = s.query(Document).filter_by(applicant_id=a.applicant_id, deleted=False).all()
        qa = prep.quality_check(a, s.get(Pathway, a.pathway_id), s.get(Applicant, a.applicant_id).profile, docs)
        if not qa["ready"]: raise HTTPException(422, {"message": "quality check has blockers", "issues": qa["issues"]})
    if b.status == "SUBMITTED":
        a.submitted_on = date.today()
    a.status = b.status
    a.reference = b.reference or a.reference; a.follow_up_on = b.follow_up_on or a.follow_up_on
    log_event(a, f"status:{b.status}", note=b.note, reference=b.reference)
    s.commit(); audit(s, u.id, "application_status", aid, status=b.status)
    workflows.emit(s, "application.status_changed", {"applicant_id": a.applicant_id, "application_id": a.id, "status": b.status, "pathway_id": a.pathway_id})
    return row(a)


# ---------- Discovery (admin) ----------
@app.post("/api/admin/discovery/{iso}", dependencies=[Depends(require("research.run"))])
def discover(iso: str, s=Depends(db)):
    c = s.get(Country, iso.upper())
    if not c: raise HTTPException(404)
    job = DiscoveryJob(country=c.iso2, status="RUNNING"); s.add(job); s.commit()
    job.result = {"items": discovery.run(c.config)}; job.status = "DONE"; s.commit()
    return row(job)


@app.get("/api/admin/review-queue", dependencies=[Depends(require("org.verify"))])
def review_queue(s=Depends(db)):
    return [row(o) for o in s.query(Organization).filter_by(needs_human_review=True)]


# ---------- Research pipeline ----------
class SourceIn(BaseModel):
    country: str; title: str; url: str; kind: str = "page"; level: int = 1
    policy_area: str = "general"; interval_hours: int = 168


@app.post("/api/admin/research/sources")
def add_source(b: SourceIn, u: User = Depends(require("research.run")), s=Depends(db)):
    if b.kind not in ("page", "feed") or not 1 <= b.level <= 5 or not b.url.startswith("https://"):
        raise HTTPException(422, "kind page|feed, level 1-5, https URL required")
    if not s.get(Country, b.country.upper()): raise HTTPException(404, "unknown country")
    src = ResearchSource(**{**b.model_dump(), "country": b.country.upper()}); s.add(src); s.commit()
    audit(s, u.id, "source_added", b.url); return row(src)


@app.get("/api/admin/research/sources")
def list_sources(u: User = Depends(require("research.run")), s=Depends(db)):
    now = datetime.utcnow()
    out = []
    for x in s.query(ResearchSource):
        r = row(x)
        r["stale"] = x.last_ok is None or (now - x.last_ok).total_seconds() > 2 * x.interval_hours * 3600
        out.append(r)
    return out


@app.post("/api/admin/research/sources/{sid}/run")
def run_source_now(sid: int, u: User = Depends(require("research.run")), s=Depends(db)):
    if not s.get(ResearchSource, sid): raise HTTPException(404)
    rid = research.execute(sid)
    audit(s, u.id, "research_run", sid)
    return row(s.get(ResearchRun, rid)) if rid else {}


@app.get("/api/admin/research/runs")
def runs(status: str | None = None, u: User = Depends(require("research.run")), s=Depends(db)):
    q = s.query(ResearchRun).order_by(ResearchRun.id.desc())
    return [row(r) for r in (q.filter_by(status=status) if status else q).limit(200)]


@app.post("/api/admin/research/runs/{rid}/retry")
def retry_run(rid: int, u: User = Depends(require("research.run")), s=Depends(db)):
    st = research.retry(rid)
    if st is None: raise HTTPException(422, "only FAILED runs can be retried")
    return {"status": st}


@app.get("/api/admin/research/results")
def results(status: str = "NEEDS_REVIEW", u: User = Depends(require("research.review")), s=Depends(db)):
    return [row(r) for r in s.query(ResearchResult).filter_by(status=status).limit(200)]


@app.post("/api/admin/research/results/{rid}/review")
def review_result(rid: int, decision: str, u: User = Depends(require("research.review")), s=Depends(db)):
    if decision not in ("APPROVED", "REJECTED"): raise HTTPException(422, "APPROVED or REJECTED")
    r = s.get(ResearchResult, rid)
    if not r: raise HTTPException(404)
    r.status, r.reviewer = decision, u.email; s.commit(); audit(s, u.id, "result_reviewed", rid, decision=decision)
    return row(r)


@app.get("/api/admin/regulatory-changes")
def reg_changes(status: str = "PENDING_REVIEW", u: User = Depends(require("catalog.verify")), s=Depends(db)):
    return [row(c) for c in s.query(RegulatoryChange).filter_by(review_status=status)]


@app.post("/api/admin/regulatory-changes/{cid}/review")
def review_change(cid: int, decision: str, note: str = "", u: User = Depends(require("catalog.verify")), s=Depends(db)):
    """CONFIRMED_IMPACT marks the country's pathways as needing re-verification (nothing is silently overwritten)."""
    if decision not in ("CONFIRMED_IMPACT", "NO_IMPACT"): raise HTTPException(422, "CONFIRMED_IMPACT or NO_IMPACT")
    c = s.get(RegulatoryChange, cid)
    if not c: raise HTTPException(404)
    c.review_status, c.reviewer_note = decision, note
    if decision == "CONFIRMED_IMPACT":
        for p in s.query(Pathway).filter_by(country=c.country): p.last_verified = None
        s.flush(); notify.notify_country_change(s, c.country)
    s.commit()
    if decision == "CONFIRMED_IMPACT": workflows.emit(s, "regulatory.confirmed", {"country": c.country, "policy_area": c.policy_area})
    audit(s, u.id, "regulatory_change_reviewed", cid, decision=decision)
    return row(c)


@app.get("/api/admin/snapshots/{sid}")
def snapshot(sid: int, u: User = Depends(require("catalog.verify")), s=Depends(db)):
    x = s.get(SourceSnapshot, sid)
    if not x: raise HTTPException(404)
    return row(x)


# ---------- Fraud prevention ----------
class CheckIn(BaseModel):
    text: str


@app.post("/api/fraud/check")
def fraud_check(b: CheckIn):
    """Public: paste an offer/message to see warning indicators. Nothing is stored."""
    hits = fraud.scan_text(b.text[:20000])
    return {"indicators": hits, "notices": fraud.NOTICES,
            "note": "Indicators are warnings, not proof. Absence of warnings does not mean an offer is genuine."}


class ReportIn(BaseModel):
    subject: str; category: str; description: str; org_id: int | None = None; evidence_url: str = ""


@app.post("/api/fraud/reports")
def report_fraud(b: ReportIn, u: User = Depends(current_user), s=Depends(db)):
    if b.category not in fraud.CATEGORIES: raise HTTPException(422, f"category must be one of {sorted(fraud.CATEGORIES)}")
    r = FraudReport(reporter_id=u.id, indicators=[h["indicator"] for h in fraud.scan_text(b.description)], **b.model_dump())
    s.add(r)
    if b.org_id and (o := s.get(Organization, b.org_id)):
        o.needs_human_review = True  # internal flag only; public status is NOT changed by an unverified report
    s.commit(); audit(s, u.id, "fraud_reported", r.id)
    workflows.emit(s, "fraud.reported", {"category": b.category, "report_id": r.id})
    return {"id": r.id, "status": r.status, "note": "Thank you. Staff will investigate. Reports do not publicly label anyone until reviewed."}


@app.get("/api/admin/fraud/reports")
def fraud_queue(status: str | None = None, u: User = Depends(require("fraud.investigate")), s=Depends(db)):
    q = s.query(FraudReport)
    return [row(r) for r in (q.filter_by(status=status) if status else q)]


class FraudUpdate(BaseModel):
    status: str; note: str = ""


@app.post("/api/admin/fraud/reports/{rid}")
def fraud_update(rid: int, b: FraudUpdate, u: User = Depends(require("fraud.investigate")), s=Depends(db)):
    if b.status not in ("INVESTIGATING", "CONFIRMED", "DISMISSED"): raise HTTPException(422, "bad status")
    r = s.get(FraudReport, rid)
    if not r: raise HTTPException(404)
    if b.status == "CONFIRMED" and not b.note.strip(): raise HTTPException(422, "confirmation requires an evidence note")
    r.status = b.status
    r.notes = [*r.notes, {"by": u.email, "at": datetime.utcnow().isoformat(timespec="seconds"), "status": b.status, "note": b.note}]
    if b.status == "CONFIRMED" and r.org_id and (o := s.get(Organization, r.org_id)):
        o.status, o.needs_human_review = "DISQUALIFIED", False
        o.evidence = [*o.evidence, {"type": "human_review", "note": f"fraud report {rid} confirmed: {b.note}", "date": date.today().isoformat()}]
    s.commit(); audit(s, u.id, "fraud_report_updated", rid, status=b.status)
    return row(r)


@app.get("/api/safety-notices")
def notices():
    return {"notices": fraud.NOTICES}


# ---------- Notifications ----------
@app.get("/api/notifications")
def my_notifications(unread: bool = False, u: User = Depends(current_user), s=Depends(db)):
    q = s.query(Notification).filter_by(user_id=u.id).order_by(Notification.id.desc())
    return [row(n) for n in (q.filter_by(read=False) if unread else q).limit(100)]


@app.post("/api/notifications/{nid}/read")
def read_notification(nid: int, u: User = Depends(current_user), s=Depends(db)):
    n = s.get(Notification, nid)
    if not n or n.user_id != u.id: raise HTTPException(404)
    n.read = True; s.commit(); return row(n)


@app.get("/api/notification-prefs")
def get_prefs(u: User = Depends(current_user), s=Depends(db)):
    return notify.prefs(s, u.id) | {"note": "Critical application, deadline, document and rule-change alerts cannot be switched off."}


@app.put("/api/notification-prefs")
def put_prefs(b: dict[str, bool], u: User = Depends(current_user), s=Depends(db)):
    return notify.set_prefs(s, u.id, b)


@app.post("/api/admin/notifications/scan")
def run_scan(u: User = Depends(require("notify.run")), s=Depends(db)):
    n = notify.scan(s); audit(s, u.id, "notification_scan", sent=n)
    return {"sent": n}


# ---------- Case management (staff) ----------
DEFAULT_STAGES = ["NEW_APPLICANT", "PROFILE_INCOMPLETE", "PROFILE_COMPLETED", "RESEARCH_IN_PROGRESS", "ELIGIBILITY_ASSESSMENT",
                  "OPPORTUNITIES_IDENTIFIED", "APPLICANT_REVIEWING_OPTIONS", "DOCUMENTS_PREPARATION", "APPLICATION_READY",
                  "APPLICATION_SUBMITTED", "AWAITING_RESPONSE", "ADDITIONAL_INFORMATION_REQUESTED", "INTERVIEW_STAGE",
                  "DECISION_RECEIVED", "SUCCESSFUL", "UNSUCCESSFUL", "ON_HOLD", "CLOSED"]


def stages(s):
    r = s.get(Setting, "case_stages")
    return r.value["stages"] if r else DEFAULT_STAGES


@app.get("/api/case-stages")
def get_stages(u: User = Depends(current_user), s=Depends(db)):
    return stages(s)


@app.put("/api/admin/case-stages")
def set_stages(b: list[str], u: User = Depends(require("case.configure")), s=Depends(db)):
    if not b or len(set(b)) != len(b) or any(not x.strip() for x in b): raise HTTPException(422, "need unique, non-empty stage names")
    in_use = {c.stage for c in s.query(Case)}
    if in_use - set(b): raise HTTPException(422, f"stages still in use cannot be removed: {sorted(in_use - set(b))}")
    s.merge(Setting(key="case_stages", value={"stages": b})); s.commit(); audit(s, u.id, "stages_changed")
    return b


def get_case(cid, s) -> Case:
    c = s.get(Case, cid)
    if not c: raise HTTPException(404)
    return c


@app.get("/api/cases")
def list_cases(stage: str | None = None, assigned_to: int | None = None, unassigned: bool = False,
               u: User = Depends(require("case.manage")), s=Depends(db)):
    q = s.query(Case)
    if stage: q = q.filter_by(stage=stage)
    if assigned_to: q = q.filter_by(assigned_to=assigned_to)
    if unassigned: q = q.filter(Case.assigned_to.is_(None))
    return [row(c) for c in q.limit(500)]


@app.get("/api/cases/{cid}")
def case_dashboard(cid: int, u: User = Depends(require("case.manage")), s=Depends(db)):
    c = get_case(cid, s); ap = s.get(Applicant, c.applicant_id)
    audit(s, u.id, "case_viewed", cid)
    apps = s.query(Application).filter_by(applicant_id=c.applicant_id).all()
    pws = s.query(Pathway).join(Country, Country.iso2 == Pathway.country).filter(Country.active.is_(True)).all()
    assess = [{k: a[k] for k in ("pathway", "country", "status", "readiness_pct", "definitive")} for a in eligibility.assess_all(pws, ap.profile)]
    open_tasks = s.query(Task).filter_by(case_id=cid, done=False).order_by(Task.due).all()
    return {"case": row(c), "profile": ap.profile, "preferred_destinations": (ap.profile.get("preferences") or {}).get("destinations", []),
            "assessment": assess, "applications": [row(a) for a in apps],
            "documents": [{"id": d.id, "doc_type": d.doc_type, "expires_on": d.expires_on.isoformat() if d.expires_on else None}
                          for d in s.query(Document).filter_by(applicant_id=c.applicant_id, deleted=False)],  # metadata only
            "upcoming_deadlines": sorted(a.deadline.isoformat() for a in apps if a.deadline and a.status in ("PREPARING", "READY")),
            "tasks": [row(t) for t in open_tasks],
            "notes": [row(n) for n in s.query(CaseNote).filter_by(case_id=cid).order_by(CaseNote.id.desc())],
            "communications": [row(x) for x in s.query(Communication).filter_by(case_id=cid).order_by(Communication.id.desc())]}


class AssignIn(BaseModel):
    staff_id: int | None


@app.post("/api/cases/{cid}/assign")
def assign(cid: int, b: AssignIn, u: User = Depends(require("case.manage")), s=Depends(db)):
    c = get_case(cid, s)
    if b.staff_id is not None:
        st = s.get(User, b.staff_id)
        if not st or not st.active or "case.manage" not in security.PERMS.get(st.role, set()):
            raise HTTPException(422, "assignee must be active staff who can manage cases")
    prev = c.assigned_to; c.assigned_to = b.staff_id; c.updated = datetime.utcnow(); s.commit()
    audit(s, u.id, "case_assigned", cid, previous=prev, now=b.staff_id)
    return row(c)


@app.post("/api/cases/{cid}/stage")
def set_case_stage(cid: int, stage: str, u: User = Depends(require("case.manage")), s=Depends(db)):
    c = get_case(cid, s)
    if stage not in stages(s): raise HTTPException(422, f"unknown stage; valid: {stages(s)}")
    if stage != c.stage:
        c.history = [*c.history, {"stage": stage, "at": datetime.utcnow().isoformat(timespec="seconds"), "by": u.id}]
        c.stage, c.updated = stage, datetime.utcnow(); s.commit(); audit(s, u.id, "case_stage", cid, stage=stage)
    return row(c)


class NoteIn(BaseModel):
    body: str


@app.post("/api/cases/{cid}/notes")
def add_note(cid: int, b: NoteIn, u: User = Depends(require("case.manage")), s=Depends(db)):
    get_case(cid, s); n = CaseNote(case_id=cid, author_id=u.id, body=b.body); s.add(n); s.commit(); return row(n)


class TaskIn(BaseModel):
    title: str; assignee_id: int | None = None; due: date | None = None


@app.post("/api/cases/{cid}/tasks")
def add_task(cid: int, b: TaskIn, u: User = Depends(require("case.manage")), s=Depends(db)):
    get_case(cid, s); t = Task(case_id=cid, **b.model_dump()); s.add(t); s.commit(); return row(t)


@app.post("/api/tasks/{tid}/done")
def finish_task(tid: int, u: User = Depends(require("case.manage")), s=Depends(db)):
    t = s.get(Task, tid)
    if not t: raise HTTPException(404)
    t.done = True; s.commit(); return row(t)


class CommIn(BaseModel):
    channel: str; direction: str; summary: str


@app.post("/api/cases/{cid}/communications")
def log_comm(cid: int, b: CommIn, u: User = Depends(require("case.manage")), s=Depends(db)):
    if b.direction not in ("inbound", "outbound"): raise HTTPException(422, "direction inbound|outbound")
    get_case(cid, s); x = Communication(case_id=cid, author_id=u.id, **b.model_dump()); s.add(x); s.commit()
    return row(x)  # records a communication; it does NOT send anything


@app.get("/api/cases/{cid}/report")
def case_report(cid: int, u: User = Depends(require("case.manage")), s=Depends(db)):
    c = get_case(cid, s); h = c.history; now = datetime.utcnow(); per: dict[str, float] = {}
    for i, e in enumerate(h):
        start = datetime.fromisoformat(e["at"]); end = datetime.fromisoformat(h[i + 1]["at"]) if i + 1 < len(h) else now
        per[e["stage"]] = per.get(e["stage"], 0) + (end - start).total_seconds() / 86400
    return {"case_id": cid, "current_stage": c.stage, "days_per_stage": {k: round(v, 2) for k, v in per.items()},
            "open_tasks": s.query(Task).filter_by(case_id=cid, done=False).count(), "history": h}


@app.get("/api/my-case")
def my_case(u: User = Depends(current_user), s=Depends(db)):
    c = s.query(Case).filter_by(applicant_id=u.applicant_id).first()
    if not c: raise HTTPException(404)
    return {"stage": c.stage, "updated": c.updated.isoformat(), "advisor_assigned": c.assigned_to is not None}  # no internal notes


# ---------- Admin dashboard (real aggregates only) ----------
CLOSED = {"SUCCESSFUL", "UNSUCCESSFUL", "CLOSED"}
REQUIRED_PROFILE = ("full_name", "dob", "nationality")


def tally(values, min_n=10):
    out: dict = {}
    for v in values: out[v or "unknown"] = out.get(v or "unknown", 0) + 1
    return {"counts": out, "total": len(values),
            "caution": f"Fewer than {min_n} records: do not draw conclusions about trends or about whole countries." if len(values) < min_n else None}


@app.get("/api/admin/dashboard")
def dashboard(u: User = Depends(require("dashboard.view")), s=Depends(db)):
    now, week, soon = datetime.utcnow(), datetime.utcnow() - timedelta(days=7), date.today() + timedelta(days=14)
    aps = s.query(Applicant).all()
    cases = s.query(Case).all()
    apps = s.query(Application).all()
    orgs = s.query(Organization).all()
    srcs = s.query(ResearchSource).all()
    try:
        s.execute(text("select 1")); dbok = True
    except Exception: dbok = False
    store_ok = os.access(vault.STORAGE if vault.STORAGE.exists() else ".", os.W_OK)
    return {
        "generated": now.isoformat(timespec="seconds"),
        "applicants": {"total": len(aps), "new_last_7_days": sum(1 for a in aps if a.created >= week),
                       "profiles_completed": sum(1 for a in aps if all(a.profile.get(k) for k in REQUIRED_PROFILE))},
        "cases": {"active": sum(1 for c in cases if c.stage not in CLOSED), "unassigned": sum(1 for c in cases if c.assigned_to is None and c.stage not in CLOSED),
                  "by_stage": tally([c.stage for c in cases], 1)["counts"]},
        "applications": {"in_progress": sum(1 for a in apps if a.status not in ("SUCCESSFUL", "UNSUCCESSFUL", "WITHDRAWN")),
                         "successful": sum(1 for a in apps if a.status == "SUCCESSFUL"),
                         "unsuccessful": sum(1 for a in apps if a.status == "UNSUCCESSFUL"),
                         "upcoming_deadlines": sum(1 for a in apps if a.deadline and date.today() <= a.deadline <= soon and a.status in ("PREPARING", "READY")),
                         "note": "Outcomes are self-reported by applicants; a job offer is not counted as an approval."},
        "organizations": {"total": len(orgs), "verified": sum(1 for o in orgs if o.status == "VERIFIED"),
                          "awaiting_verification": sum(1 for o in orgs if o.status in ("UNVERIFIED", "PARTIALLY VERIFIED")),
                          "needs_human_review": sum(1 for o in orgs if o.needs_human_review)},
        "opportunities": {"total": s.query(Opportunity).count(), "open_verified": s.query(Opportunity).filter(Opportunity.status == "OPEN", Opportunity.verification.in_(["VERIFIED", "PARTIALLY VERIFIED"])).count()},
        "countries": {"activated": s.query(Country).filter_by(active=True, is_destination=True).count()},
        "research": {"sources": len(srcs), "stale_sources": sum(1 for x in srcs if x.last_ok is None or (now - x.last_ok).total_seconds() > 2 * x.interval_hours * 3600),
                     "failed_runs": s.query(ResearchRun).filter_by(status="FAILED").count(), "dead_runs": s.query(ResearchRun).filter_by(status="DEAD").count(),
                     "leads_awaiting_review": s.query(ResearchResult).filter_by(status="NEEDS_REVIEW").count(),
                     "regulatory_changes_pending": s.query(RegulatoryChange).filter_by(review_status="PENDING_REVIEW").count()},
        "fraud": {"open_reports": s.query(FraudReport).filter(FraudReport.status.in_(["NEW", "INVESTIGATING"])).count()},
        "health": {"database": dbok, "document_storage_writable": store_ok,
                   "scheduler": "enabled" if os.environ.get("SCHEDULER") == "1" else "disabled",
                   "document_encryption_key": "configured" if os.environ.get("DOC_ENCRYPTION_KEY") else "DEV FALLBACK KEY IN USE",
                   "email_provider": "NOT_CONFIGURED" if type(notify.EMAIL) is notify.EmailAdapter else "configured",
                   "malware_scanner": "NOT_CONFIGURED"},
        "analytics": {"applicants_by_nationality": tally([a.profile.get("nationality") for a in aps]),
                      "applications_by_pathway": tally([a.pathway_id for a in apps])},
    }


# ---------- Staff management ----------
@app.get("/api/admin/staff")
def list_staff(u: User = Depends(require("user.manage")), s=Depends(db)):
    return [{"id": x.id, "email": x.email, "role": x.role, "active": x.active, "open_cases": s.query(Case).filter_by(assigned_to=x.id).count(), "is_agent": x.role.startswith("agent_")}
            for x in s.query(User).filter(User.role != "applicant")]


class StaffPatch(BaseModel):
    role: str | None = None; active: bool | None = None


@app.patch("/api/admin/staff/{uid}")
def patch_staff(uid: int, b: StaffPatch, u: User = Depends(require("user.manage")), s=Depends(db)):
    t = s.get(User, uid)
    if not t or t.role == "applicant": raise HTTPException(404)
    if t.role.startswith("agent_") or (b.role or "").startswith("agent_"): raise HTTPException(422, "agents are managed under /api/admin/agents")
    if t.id == u.id: raise HTTPException(422, "you cannot change your own role or status")
    if b.role is not None and (b.role not in security.ROLES or b.role == "applicant"): raise HTTPException(422, "bad role")
    leaving = t.role == "super_admin" and ((b.role and b.role != "super_admin") or b.active is False)
    if leaving and s.query(User).filter_by(role="super_admin", active=True).count() <= 1:
        raise HTTPException(422, "cannot remove the last active super administrator")
    prev = {"role": t.role, "active": t.active}
    if b.role is not None: t.role = b.role
    if b.active is not None:
        t.active = b.active
        if not b.active: s.query(AuthSession).filter_by(user_id=t.id).delete()  # revoke sessions immediately
    s.commit(); audit(s, u.id, "staff_changed", uid, before=prev, role=t.role, active=t.active)
    return {"id": t.id, "role": t.role, "active": t.active}


# ---------- Workflows ----------
class WorkflowIn(BaseModel):
    name: str; trigger: str; conditions: list[dict] = []; steps: list[dict]; active: bool = True


@app.get("/api/admin/workflows")
def list_workflows(u: User = Depends(require("workflow.manage")), s=Depends(db)):
    return {"workflows": [row(w) for w in s.query(Workflow)], "triggers": sorted(workflows.TRIGGERS), "step_types": sorted(workflows.STEP_TYPES)}


@app.post("/api/admin/workflows")
def save_workflow(b: WorkflowIn, u: User = Depends(require("workflow.manage")), s=Depends(db)):
    try: workflows.validate(b.trigger, b.conditions, b.steps)
    except ValueError as e: raise HTTPException(422, str(e))
    w = s.query(Workflow).filter_by(name=b.name).first() or Workflow(name=b.name)
    w.trigger, w.conditions, w.steps, w.active = b.trigger, b.conditions, b.steps, b.active
    s.add(w); s.commit(); audit(s, u.id, "workflow_saved", w.id)
    return row(w)


@app.get("/api/admin/workflow-runs")
def wf_runs(status: str | None = None, u: User = Depends(require("workflow.manage")), s=Depends(db)):
    q = s.query(WorkflowRun).order_by(WorkflowRun.id.desc())
    return [row(r) for r in (q.filter_by(status=status) if status else q).limit(200)]


@app.post("/api/admin/workflow-runs/{rid}/retry")
def wf_retry(rid: int, u: User = Depends(require("workflow.manage")), s=Depends(db)):
    try: return row(workflows.retry(s, rid))
    except ValueError as e: raise HTTPException(422, str(e))


@app.post("/api/admin/workflow-runs/{rid}/decide")
def wf_decide(rid: int, approve: bool, u: User = Depends(current_user), s=Depends(db)):
    try: r = workflows.decide(s, rid, approve, u)
    except PermissionError as e: raise HTTPException(403, f"missing permission: {e}")
    except ValueError as e: raise HTTPException(422, str(e))
    audit(s, u.id, "workflow_decision", rid, approve=approve)
    return row(r)


@app.post("/api/admin/workflow-runs/tick")
def wf_tick(u: User = Depends(require("workflow.manage")), s=Depends(db)):
    return {"resumed": workflows.tick(s)}


# ---------- AI agents ----------
@app.get("/api/admin/agents")
def list_agents(u: User = Depends(require("agent.manage")), s=Depends(db)):
    from .agents.tools import TOOLS
    return {"paused": agent_rt.paused(s), "model": "no model configured: agents run deterministic handlers",
            "agents": [row(a) | {"pending_approvals": s.query(ApprovalRequest).filter_by(agent_key=a.key, status="PENDING").count(),
                                 "queued_tasks": s.query(AgentTask).filter_by(agent_key=a.key, status="QUEUED").count()} for a in s.query(AgentConfig)],
            "tools": {k: {"risk": v.risk, "human_perm": v.human_perm} for k, v in TOOLS.items()}}


class AgentPatch(BaseModel):
    active: bool | None = None; responsibilities: str | None = None; allowed_tools: list[str] | None = None; max_steps: int | None = None


@app.patch("/api/admin/agents/{key}")
def patch_agent(key: str, b: AgentPatch, u: User = Depends(require("agent.manage")), s=Depends(db)):
    from .agents.tools import TOOLS
    a = s.get(AgentConfig, key)
    if not a: raise HTTPException(404)
    if b.allowed_tools is not None:
        if any(t not in TOOLS for t in b.allowed_tools): raise HTTPException(422, "unknown tool")
        a.allowed_tools = b.allowed_tools
    if b.active is not None: a.active = b.active
    if b.responsibilities is not None: a.responsibilities = b.responsibilities
    if b.max_steps is not None:
        if not 1 <= b.max_steps <= 200: raise HTTPException(422, "max_steps 1-200")
        a.max_steps = b.max_steps
    s.commit(); audit(s, u.id, "agent_configured", key, change=b.model_dump(exclude_none=True))
    return row(a)


@app.post("/api/admin/agents/pause-all")
def pause_all(paused: bool = True, u: User = Depends(require("agent.manage")), s=Depends(db)):
    agent_rt.set_paused(s, paused); audit(s, u.id, "agents_paused", paused=paused)
    return {"paused": paused}


class AgentTaskIn(BaseModel):
    agent: str; type: str; input: dict = {}; run_now: bool = False


@app.post("/api/admin/agents/tasks")
def create_agent_task(b: AgentTaskIn, u: User = Depends(require("agent.manage")), s=Depends(db)):
    if b.agent not in agent_rt.AGENT_TASKS or b.type not in agent_rt.AGENT_TASKS[b.agent] or not s.get(AgentConfig, b.agent):
        raise HTTPException(422, "unknown agent/task type")
    t = AgentTask(agent_key=b.agent, type=b.type, input=b.input, assigned_by=u.id); s.add(t); s.commit()
    audit(s, u.id, "agent_task_assigned", t.id, agent=b.agent)
    if b.run_now: agent_rt.run_task(s, t.id); s.refresh(t)
    return row(t)


@app.get("/api/admin/agents/tasks")
def agent_tasks(status: str | None = None, u: User = Depends(require("agent.manage")), s=Depends(db)):
    q = s.query(AgentTask).order_by(AgentTask.id.desc())
    return [row(t) for t in (q.filter_by(status=status) if status else q).limit(200)]


@app.post("/api/admin/agents/tasks/{tid}/run")
def run_agent_task(tid: int, u: User = Depends(require("agent.manage")), s=Depends(db)):
    t = agent_rt.run_task(s, tid)
    if not t: raise HTTPException(422, "task not runnable")
    return row(t)


@app.get("/api/admin/approvals")
def approvals(status: str = "PENDING", u: User = Depends(current_user), s=Depends(db)):
    from .agents.tools import TOOLS
    mine = security.PERMS.get(u.role, set())
    if not (mine & {"org.verify", "research.review", "fraud.investigate", "agent.manage"}): raise HTTPException(403, "no approval permissions")
    out = []
    for r in s.query(ApprovalRequest).filter_by(status=status).order_by(ApprovalRequest.id):
        out.append(row(r) | {"can_decide": TOOLS[r.tool].human_perm in mine, "needs_permission": TOOLS[r.tool].human_perm})
    return out


@app.post("/api/admin/approvals/{aid}/decide")
def decide_approval(aid: int, approve: bool, reason: str = "", u: User = Depends(current_user), s=Depends(db)):
    try: return agent_rt.decide(s, aid, approve, u, reason)
    except PermissionError as e: raise HTTPException(403, f"missing permission: {e}")
    except ValueError as e: raise HTTPException(422, str(e))


@app.get("/api/admin/outreach-drafts")
def drafts(u: User = Depends(require("org.manage")), s=Depends(db)):
    return [row(d) for d in s.query(OutreachDraft).order_by(OutreachDraft.id.desc()).limit(100)]


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")
