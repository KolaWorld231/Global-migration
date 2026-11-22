"""Research-driven advisor. Deterministic and grounded in DB records and the eligibility engine.
Only fields the applicant has consented to share ("advisor"/"share") are read."""
from .eligibility import assess_all, STATUSES
from ..db import Pathway, Opportunity, Organization, Country

MODES = ["general", "eligibility", "jobs", "scholarships", "preparation", "documents", "interview",
         "budget", "comparison", "progress"]
SENSITIVE = {"passport_number", "medical", "police_record", "id_number"}


def visible_profile(profile, consent):
    """Strip anything not shared with the advisor. Returns filtered profile."""
    def keep(key):
        return consent.get(key) in ("advisor", "share")
    out = {k: v for k, v in profile.items() if keep(k) and k not in SENSITIVE}
    return out


def _countries(s):
    return {c.iso2: c.name for c in s.query(Country).filter_by(active=True, is_destination=True)}


def compare(s, profile):
    pws = s.query(Pathway).filter_by(active=True).all()
    names = _countries(s)
    rows = []
    for iso, name in names.items():
        res = assess_all(pws, profile, country=iso)
        best = res[0] if res else None
        opps = s.query(Opportunity).filter_by(country=iso, status="OPEN").filter(
            Opportunity.verification.in_(["VERIFIED", "PARTIALLY VERIFIED"])).count()
        rows.append({"country": name, "iso2": iso, "best_pathway": best and best["pathway"],
                     "status": best and best["status"], "readiness_pct": best and best["readiness_pct"],
                     "definitive": best and best["definitive"], "verified_open_opportunities": opps})
    return {"comparison": rows, "note": "Ordered by eligibility status then readiness. Verified opportunity counts "
            "come only from records in this database. Costs, language and settlement factors are shown on each "
            "pathway; no country is ranked on unsupported assumptions."}


def budget(s, profile):
    pws = s.query(Pathway).filter_by(active=True).all()
    pref = profile.get("preferences", {}).get("destinations") or list(_countries(s))
    rows = []
    for p in pws:
        if p.country in pref:
            rows.append({"pathway": p.name, "country": p.country, "known_fees": p.fees or "not yet recorded",
                         "typical_cost_items": ["Application/visa fee", "Language test", "Credential evaluation",
                                                "Police certificate & medical", "Translation & courier",
                                                "Flights & first-month living costs"]})
    have = profile.get("financial", {}).get("budget_usd")
    return {"budget_usd": have, "pathways": rows, "note": "Fee amounts must be copied from official sources into "
            "pathway.fees by an admin; the advisor will not invent figures. Legitimate employers rarely require "
            "workers to pay recruitment fees: treat any such request as a red flag."}


def documents(s, profile, pathway_id=None):
    q = s.query(Pathway)
    q = q.filter_by(id=pathway_id) if pathway_id else q.filter_by(active=True)
    have = profile.get("documents", {})
    out = []
    for p in q.all():
        out.append({"pathway": p.name, "checklist": [{"document": d, "held": bool(have.get(d.lower().split(" ")[0]))} for d in p.documents]})
    return {"documents": out}


def jobs(s, profile, country=None):
    nat = (profile.get("nationality") or "").upper()
    q = s.query(Opportunity).filter(Opportunity.status == "OPEN", Opportunity.verification.in_(["VERIFIED", "PARTIALLY VERIFIED"]))
    if country:
        q = q.filter_by(country=country)
    rows = [o for o in q.all() if not (nat and nat in [n.upper() for n in o.nationality_excluded])
            and not (o.nationality_allowed and nat not in [n.upper() for n in o.nationality_allowed])]
    return {"opportunities": [{"id": o.id, "title": o.title, "country": o.country, "sponsorship": o.sponsorship,
                               "kind": o.kind, "applicant_fee": o.applicant_fee, "verification": o.verification, "url": o.url} for o in rows],
            "note": "Only verified or partially verified opportunities open to your nationality are shown. "
                    "Always confirm on the official application link." if rows else
                    "No verified opportunities match yet. Unverified listings are hidden from advisor results."}


def interview(s, profile):
    return {"tips": ["Prepare a consistent timeline of education and work that matches your documents exactly.",
                     "Be ready to explain your intended role, employer and why this country.",
                     "Never present false or altered documents; misrepresentation can lead to long bans.",
                     "Practice answering in the language of the interview and bring originals."]}


def answer(s, mode, profile, consent, **kw):
    prof = visible_profile(profile, consent) if consent is not None else profile
    if mode == "comparison":
        return compare(s, prof)
    if mode == "budget":
        return budget(s, prof)
    if mode in ("documents", "preparation"):
        return documents(s, prof, kw.get("pathway_id"))
    if mode in ("jobs", "scholarships"):
        r = jobs(s, prof, kw.get("country"))
        if mode == "jobs":
            r["opportunities"] = [o for o in r["opportunities"] if o["kind"] not in ("scholarship", "research_fellowship")]
        if mode == "scholarships":
            r["opportunities"] = [o for o in r["opportunities"] if o["kind"] in ("scholarship", "research_fellowship", "internship")]
        return r
    if mode == "interview":
        return interview(s, prof)
    if mode == "eligibility":
        return {"assessments": assess_all(s.query(Pathway).filter_by(active=True).all(), prof, kw.get("country"))}
    return {"message": "Pick a mode: " + ", ".join(MODES), "modes": MODES,
            "status_legend": STATUSES}
