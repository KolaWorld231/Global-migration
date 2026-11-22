"""Applicant->opportunity matching. Uses only verified/partially verified, open, unexpired listings.
Every match lists the criteria it satisfied and the gaps, so it is explainable (no opaque ranking)."""
from datetime import date
from ..db import Opportunity
from .eligibility import EDU_ORDER, get_path


def match(s, profile, limit=50):
    today = date.today()
    nat = (profile.get("nationality") or "").upper()
    edu = get_path(profile, "education.level")
    yrs = get_path(profile, "employment.total_years")
    prefs = [c.upper() for c in (get_path(profile, "preferences.destinations") or [])]
    out = []
    q = s.query(Opportunity).filter(Opportunity.status == "OPEN",
                                    Opportunity.verification.in_(["VERIFIED", "PARTIALLY VERIFIED"]))
    for o in q:
        if o.deadline and o.deadline < today:
            continue
        if nat and (nat in [n.upper() for n in o.nationality_excluded] or
                    (o.nationality_allowed and nat not in [n.upper() for n in o.nationality_allowed])):
            continue
        met, gaps = [], []
        if edu in EDU_ORDER:
            (met if EDU_ORDER.index(edu) >= EDU_ORDER.index(o.min_education) else gaps).append(f"education >= {o.min_education}")
        else:
            gaps.append("education unknown")
        if yrs is not None:
            (met if yrs >= o.min_experience_years else gaps).append(f"experience >= {o.min_experience_years}y")
        else:
            gaps.append("experience unknown")
        if prefs and o.country in prefs:
            met.append("preferred destination")
        if get_path(profile, "occupation") and o.occupation and get_path(profile, "occupation").lower() in o.occupation.lower():
            met.append("occupation match")
        if o.applicant_fee:
            gaps.append("listing charges applicants a fee: treat with caution")
        out.append({"opportunity_id": o.id, "title": o.title, "country": o.country, "verification": o.verification,
                    "met": met, "gaps": gaps, "score": len(met) - 2 * len([g for g in gaps if "unknown" not in g]),
                    "url": o.url, "note": "Score is a sort aid only. Confirm eligibility on the official listing."})
    return sorted(out, key=lambda x: -x["score"])[:limit]
