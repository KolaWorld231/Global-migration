import os, secrets
from datetime import date
from typing import Any
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .db import (init_db, SessionLocal, Country, Pathway, Organization, Opportunity, Applicant, Case, DiscoveryJob)
from .seed.loader import seed
from .engine import eligibility, verification, advisor, discovery

app = FastAPI(title="Global Pathways", version="0.1.0")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(16)
if not os.environ.get("ADMIN_TOKEN"):
    print(f"[global-pathways] ADMIN_TOKEN not set; generated for this run: {ADMIN_TOKEN}")


@app.on_event("startup")
def _startup():
    init_db()
    seed()


def db():
    with SessionLocal() as s:
        yield s


def admin(x_admin_token: str = Header(default="")):
    if not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(401, "admin token required")


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


@app.post("/api/admin/countries", dependencies=[Depends(admin)])
def add_country(c: CountryIn, s=Depends(db)):
    iso = c.iso2.upper()
    if s.get(Country, iso): raise HTTPException(409, "country exists")
    s.add(Country(iso2=iso, iso3=c.iso3.upper(), name=c.name, is_destination=c.is_destination,
                  is_origin=c.is_origin, config=c.config, active=False))
    s.commit()
    return {"iso2": iso, "active": False, "next": "add pathways, then POST /api/admin/countries/{iso}/activate"}


@app.post("/api/admin/countries/{iso}/activate", dependencies=[Depends(admin)])
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


@app.post("/api/admin/countries/{iso}/verify", dependencies=[Depends(admin)])
def verify_country(iso: str, reliability: str = "official", s=Depends(db)):
    c = s.get(Country, iso.upper()); c.last_verified = date.today(); c.source_reliability = reliability; s.commit()
    return row(c)


class PathwayIn(BaseModel):
    id: str; country: str; name: str; category: str
    official_url: str = ""; requirements: list[dict] = []; documents: list[str] = []
    fees: dict = {}; excluded_nationalities: list[str] = []; allowed_nationalities: list[str] = []


@app.post("/api/admin/pathways", dependencies=[Depends(admin)])
def upsert_pathway(p: PathwayIn, s=Depends(db)):
    if not s.get(Country, p.country.upper()): raise HTTPException(404, "unknown country")
    s.merge(Pathway(**{**p.model_dump(), "country": p.country.upper()})); s.commit()
    return {"ok": True}


@app.post("/api/admin/pathways/{pid}/verify", dependencies=[Depends(admin)])
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


@app.post("/api/admin/organizations", dependencies=[Depends(admin)])
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


@app.post("/api/admin/organizations/{oid}/evidence", dependencies=[Depends(admin)])
def add_evidence(oid: int, e: EvidenceIn, s=Depends(db)):
    o = s.get(Organization, oid)
    if not o: raise HTTPException(404)
    if e.type in verification.WEIGHTS or e.type == "note":
        o.evidence = [*o.evidence, {"type": e.type, "url": e.url, "note": e.note, "date": date.today().isoformat()}]
    flags = set(o.details.get("flags", [])) | set(e.flags)
    o.details = {**o.details, "flags": sorted(flags)}
    s.commit()
    return row(o)


@app.post("/api/admin/organizations/{oid}/evaluate", dependencies=[Depends(admin)])
def evaluate_org(oid: int, s=Depends(db)):
    o = s.get(Organization, oid)
    if not o: raise HTTPException(404)
    out = verification.evaluate(o, o.details.get("flags", []))
    verification.apply(o, out); s.commit()
    return {"organization": row(o), "outcome": out}


@app.post("/api/admin/organizations/{oid}/decision", dependencies=[Depends(admin)])
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


@app.post("/api/admin/opportunities", dependencies=[Depends(admin)])
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


# ---------- Applicants, assessment, advisor, cases ----------
class ApplicantIn(BaseModel):
    profile: dict[str, Any] = {}
    consent: dict[str, str] = {}


@app.post("/api/applicants")
def create_applicant(a: ApplicantIn, s=Depends(db)):
    ap = Applicant(profile=a.profile, consent=a.consent); s.add(ap); s.commit()
    return {"id": ap.id}


@app.put("/api/applicants/{aid}")
def update_applicant(aid: int, a: ApplicantIn, s=Depends(db)):
    ap = s.get(Applicant, aid)
    if not ap: raise HTTPException(404)
    ap.profile = {**ap.profile, **a.profile}; ap.consent = {**ap.consent, **a.consent}; s.commit()
    return {"id": ap.id, "profile": ap.profile, "consent": ap.consent}


@app.get("/api/applicants/{aid}")
def get_applicant(aid: int, s=Depends(db)):
    ap = s.get(Applicant, aid)
    if not ap: raise HTTPException(404)
    return {"id": ap.id, "profile": ap.profile, "consent": ap.consent}


@app.delete("/api/applicants/{aid}")
def delete_applicant(aid: int, s=Depends(db)):
    ap = s.get(Applicant, aid)
    if ap:
        s.query(Case).filter_by(applicant_id=aid).delete(); s.delete(ap); s.commit()
    return {"deleted": True}


@app.get("/api/applicants/{aid}/assessment")
def assessment(aid: int, country: str | None = None, s=Depends(db)):
    ap = s.get(Applicant, aid)
    if not ap: raise HTTPException(404)
    pws = s.query(Pathway).join(Country, Country.iso2 == Pathway.country).filter(Country.active.is_(True)).all()
    return eligibility.assess_all(pws, ap.profile, country.upper() if country else None)


class AdviceIn(BaseModel):
    mode: str = "general"; country: str | None = None; pathway_id: str | None = None


@app.post("/api/applicants/{aid}/advisor")
def advise(aid: int, body: AdviceIn, s=Depends(db)):
    ap = s.get(Applicant, aid)
    if not ap: raise HTTPException(404)
    if body.mode not in advisor.MODES: raise HTTPException(422, f"mode must be one of {advisor.MODES}")
    # Advisor sees only fields the applicant opted in to share.
    return advisor.answer(s, body.mode, ap.profile, ap.consent, country=body.country, pathway_id=body.pathway_id)


@app.post("/api/applicants/{aid}/cases/{pid}")
def open_case(aid: int, pid: str, s=Depends(db)):
    p = s.get(Pathway, pid)
    if not p or not s.get(Applicant, aid): raise HTTPException(404)
    c = Case(applicant_id=aid, pathway_id=pid, checklist={d: "NOT_STARTED" for d in p.documents}, log=[])
    s.add(c); s.commit()
    return row(c)


@app.get("/api/applicants/{aid}/cases")
def cases(aid: int, s=Depends(db)):
    return [row(c) for c in s.query(Case).filter_by(applicant_id=aid)]


STAGES = ["PLANNING", "DOCUMENTS", "SUBMITTED", "DECISION", "CLOSED"]


@app.patch("/api/cases/{cid}")
def update_case(cid: int, stage: str | None = None, document: str | None = None, doc_status: str | None = None,
                s=Depends(db)):
    c = s.get(Case, cid)
    if not c: raise HTTPException(404)
    if stage:
        if stage not in STAGES: raise HTTPException(422, f"stage must be one of {STAGES}")
        c.stage = stage; c.log = [*c.log, f"{date.today()}: stage -> {stage}"]
    if document:
        if document not in c.checklist: raise HTTPException(422, "unknown document")
        c.checklist = {**c.checklist, document: doc_status or "DONE"}
    s.commit()
    return row(c)


# ---------- Discovery (admin) ----------
@app.post("/api/admin/discovery/{iso}", dependencies=[Depends(admin)])
def discover(iso: str, s=Depends(db)):
    c = s.get(Country, iso.upper())
    if not c: raise HTTPException(404)
    job = DiscoveryJob(country=c.iso2, status="RUNNING"); s.add(job); s.commit()
    job.result = {"items": discovery.run(c.config)}; job.status = "DONE"; s.commit()
    return row(job)


@app.get("/api/admin/review-queue", dependencies=[Depends(admin)])
def review_queue(s=Depends(db)):
    return [row(o) for o in s.query(Organization).filter_by(needs_human_review=True)]


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")
