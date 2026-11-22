"""Organization verification: evidence-based, conservative. A score never proves legitimacy.

Evidence item: {"type": <see WEIGHTS>, "url": "...", "note": "...", "date": "YYYY-MM-DD"}
"""
from datetime import date
from urllib.parse import urlparse

# Authoritative evidence counts more than self-published evidence.
AUTHORITATIVE = {"government_registry", "licence_register", "official_government_listing", "regulator_listing"}
SUPPORTING = {"domain_whois_match", "physical_presence", "independent_reference", "job_ad_on_official_site",
              "published_recruitment_history", "contact_matches_official_site"}
WEIGHTS = {**{t: 25 for t in AUTHORITATIVE}, **{t: 10 for t in SUPPORTING}}
RED_FLAGS = {"asks_upfront_fee": 40, "guarantees_visa": 40, "scam_warning_found": 60, "impersonates_other_org": 80,
             "free_email_only": 15, "whatsapp_only_contact": 15, "domain_mismatch": 25, "pressure_tactics": 25,
             "no_verifiable_address": 15}
HARD_STOP = {"impersonates_other_org"}


def detect_flags(org_details, website, evidence_flags=()):
    flags = set(evidence_flags)
    d = org_details or {}
    email = (d.get("email") or "").lower()
    if email and website:
        host = urlparse(website if "//" in website else "//" + website).hostname or ""
        host = host.removeprefix("www.")
        if host and not email.endswith("@" + host) and not email.endswith("." + host):
            if email.split("@")[-1] in {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com"}:
                flags.add("free_email_only")
            else:
                flags.add("domain_mismatch")
    if d.get("applicant_fee") or d.get("recruitment_fee_charged_to_applicant"):
        flags.add("asks_upfront_fee")
    if d.get("guarantees_visa"):
        flags.add("guarantees_visa")
    return flags


def evaluate(org, flags_extra=()):
    """Return proposed verification outcome. Does not touch the DB."""
    kinds = {e["type"] for e in org.evidence}
    auth = kinds & AUTHORITATIVE
    sup = kinds & SUPPORTING
    flags = detect_flags(org.details, org.website, flags_extra)
    score = min(100, sum(WEIGHTS[k] for k in kinds if k in WEIGHTS))
    penalty = sum(RED_FLAGS[f] for f in flags)
    confidence = max(0, score - penalty)
    reasons = [f"authoritative evidence: {sorted(auth) or 'none'}", f"supporting evidence: {sorted(sup) or 'none'}",
               f"red flags: {sorted(flags) or 'none'}"]
    if flags & HARD_STOP or "scam_warning_found" in flags:
        status, review = "SUSPICIOUS", True
    elif penalty >= 40:
        status, review = "SUSPICIOUS", True
    elif auth and len(sup) >= 2 and not flags:
        status, review = "VERIFIED", False
    elif auth or len(sup) >= 2:
        status, review = "PARTIALLY VERIFIED", bool(flags)
    else:
        status, review = "UNVERIFIED", bool(flags)
    if status == "VERIFIED" and org.details.get("claims_sponsorship") and "job_ad_on_official_site" not in kinds:
        status, review = "PARTIALLY VERIFIED", True
        reasons.append("sponsorship claim has no supporting job advertisement")
    return {"status": status, "confidence": confidence, "needs_human_review": review, "flags": sorted(flags),
            "reasons": reasons, "note": "Confidence is a triage aid, not proof of legitimacy.", "checked": date.today().isoformat()}


def apply(org, outcome, reviewer=None):
    """Persist outcome. DISQUALIFIED/INACTIVE/EXPIRED are only set by a human reviewer."""
    org.status = outcome["status"]
    org.confidence = outcome["confidence"]
    org.needs_human_review = outcome["needs_human_review"]
    org.last_verified = date.today()
