import os, secrets
from datetime import date, datetime
from typing import Any
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .db import (init_db, SessionLocal, Country, Pathway, Organization, Opportunity, Applicant, Case, DiscoveryJob)
from .seed.loader import seed
from .engine import eligibility, verification, advisor, discovery, matching
from . import security, vault, prep
from .security import require, current_user, audit
from .db import User, AuditLog, Document, Application
from fastapi import UploadFile, File, Form

app = FastAPI(title="Global Pathways", version="0.1.0")
@app.on_event("startup")
def _startup():
    init_db()
    seed()
    bootstrap_admin()


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
    s.add(u); s.commit(); audit(s, u.id, "register")
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
    if b.role not in security.ROLES or b.role == "applicant": raise HTTPException(422, "bad role")
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
    s.query(Case).filter_by(applicant_id=aid).delete(); s.query(Application).filter_by(applicant_id=aid).delete()
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


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")
