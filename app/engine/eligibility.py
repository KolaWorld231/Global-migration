"""Declarative, country-agnostic eligibility engine.

A requirement is data:
  {"id","category","label","field","op","value","kind":"official"|"readiness","source","blocking":bool}
`field` is a dotted path into the applicant profile. Adding a country never touches this file.
"""
from datetime import date, timedelta

EDU_ORDER = ["none", "primary", "secondary", "vocational", "diploma", "bachelor", "master", "doctorate"]
STALE_AFTER = timedelta(days=365)
STATUSES = ["POTENTIALLY ELIGIBLE", "ELIGIBLE SUBJECT TO VERIFICATION", "ADDITIONAL REQUIREMENTS NEEDED",
            "CURRENTLY NOT ELIGIBLE", "INSUFFICIENT INFORMATION"]


def get_path(d, path):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d or d[part] in (None, ""):
            return None
        d = d[part]
    return d


def age_from_dob(dob, today=None):
    today = today or date.today()
    try:
        b = date.fromisoformat(dob)
    except (TypeError, ValueError):
        return None
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def derive(profile, today=None):
    p = dict(profile)
    p["age"] = age_from_dob(profile.get("dob"), today)
    p["_edu_rank"] = EDU_ORDER.index(profile["education"]["level"]) if get_path(profile, "education.level") in EDU_ORDER else None
    return p


def check(req, p):
    """Returns (result, applicant_value) where result in met|unmet|unknown."""
    op, want = req["op"], req.get("value")
    field = req["field"]
    val = get_path(p, field)
    if val is None:
        return "unknown", None
    try:
        if field == "education.level" and op == "gte":
            ok = EDU_ORDER.index(val) >= EDU_ORDER.index(want)
        elif op == "gte":
            ok = val >= want
        elif op == "lte":
            ok = val <= want
        elif op == "eq":
            ok = val == want
        elif op == "in":
            ok = val in want
        elif op == "not_in":
            ok = val not in want
        elif op == "truthy":
            ok = bool(val)
        else:
            return "unknown", val
    except (ValueError, TypeError):
        return "unknown", val
    return ("met" if ok else "unmet"), val


def assess_pathway(pathway, profile, today=None):
    today = today or date.today()
    p = derive(profile, today)
    rows = []
    nat = (profile.get("nationality") or "").upper()
    nat_row = None
    if nat:
        blocked = nat in [n.upper() for n in pathway.excluded_nationalities]
        not_listed = pathway.allowed_nationalities and nat not in [n.upper() for n in pathway.allowed_nationalities]
        res = "unmet" if (blocked or not_listed) else "met"
        nat_row = {"category": "Nationality eligibility", "requirement": "Nationality accepted for this pathway",
                   "kind": "official", "applicant_value": nat, "result": res, "blocking": True,
                   "source": pathway.official_url, "recommended_action":
                   "Pathway lists your nationality as ineligible; look at alternatives." if res == "unmet" else ""}
    else:
        nat_row = {"category": "Nationality eligibility", "requirement": "Nationality accepted for this pathway",
                   "kind": "official", "applicant_value": None, "result": "unknown", "blocking": True,
                   "source": pathway.official_url, "recommended_action": "Add your nationality to your profile."}
    rows.append(nat_row)
    for r in pathway.requirements:
        res, val = check(r, p)
        action = ""
        if res == "unknown":
            action = f"Provide: {r['field']}"
        elif res == "unmet":
            action = r.get("improve", "Review how to meet this requirement before applying.")
        rows.append({"category": r["category"], "requirement": r["label"], "kind": r.get("kind", "official"),
                     "applicant_value": val, "result": res, "blocking": r.get("blocking", True),
                     "source": r.get("source") or pathway.official_url, "recommended_action": action})

    unmet_block = [x for x in rows if x["result"] == "unmet" and x["blocking"]]
    unmet_soft = [x for x in rows if x["result"] == "unmet" and not x["blocking"]]
    unknown = [x for x in rows if x["result"] == "unknown" and x["blocking"]]
    official = [x for x in rows if x["kind"] == "official"]
    met = [x for x in official if x["result"] == "met"]

    if unmet_block:
        status = "CURRENTLY NOT ELIGIBLE"
    elif unknown:
        status = "INSUFFICIENT INFORMATION" if len(unknown) >= len(rows) / 2 else "ADDITIONAL REQUIREMENTS NEEDED"
    elif unmet_soft:
        status = "ADDITIONAL REQUIREMENTS NEEDED"
    else:
        status = "POTENTIALLY ELIGIBLE"

    stale = pathway.last_verified is None or (today - pathway.last_verified) > STALE_AFTER
    warnings, definitive = [], True
    if stale:
        definitive = False
        warnings.append("Rules for this pathway are unverified or older than 12 months. Preliminary result only; "
                        "confirm every requirement on the official source before relying on it.")
        if status in ("POTENTIALLY ELIGIBLE", "CURRENTLY NOT ELIGIBLE"):
            status = "ELIGIBLE SUBJECT TO VERIFICATION" if status == "POTENTIALLY ELIGIBLE" else "INSUFFICIENT INFORMATION"
    elif status == "POTENTIALLY ELIGIBLE":
        status = "POTENTIALLY ELIGIBLE"
    readiness = round(100 * len(met) / len(official)) if official else 0
    return {
        "pathway_id": pathway.id, "pathway": pathway.name, "country": pathway.country, "category": pathway.category,
        "status": status, "definitive": definitive, "readiness_pct": readiness,
        "readiness_note": "Readiness = share of listed official requirements currently met. It is the system's "
                          "own indicator, not an official score, and not a prediction of approval.",
        "warnings": warnings, "requirements": rows, "official_url": pathway.official_url,
        "disclaimer": "No outcome is guaranteed. Only the immigration authority can decide an application.",
    }


def assess_all(pathways, profile, country=None, today=None):
    out = [assess_pathway(pw, profile, today) for pw in pathways if pw.active and (not country or pw.country == country)]
    order = {s: i for i, s in enumerate(STATUSES)}
    return sorted(out, key=lambda a: (order[a["status"]], -a["readiness_pct"]))
