import os, tempfile
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/t.db"
os.environ["ADMIN_TOKEN"] = "t"
from datetime import date
from fastapi.testclient import TestClient
from app.main import app

H = {"X-Admin-Token": "t"}
PROFILE = {"nationality": "LR", "dob": "1995-01-01", "education": {"level": "bachelor"},
           "employment": {"total_years": 4}, "language": {"english_evidence": True},
           "documents": {"passport": True}, "preferences": {"has_job_offer": False}, "financial": {"budget_usd": 2000}}


def test_flow():
    with TestClient(app) as c:
        assert len(c.get("/api/countries?role=destination").json()) == 5
        # new country without core changes
        assert c.post("/api/admin/countries", json={"iso2": "DE", "iso3": "DEU", "name": "Germany", "is_destination": True}, headers=H).status_code == 200
        assert c.post("/api/admin/countries/DE/activate", headers=H).status_code == 422  # not configured yet
        # unauthorised
        assert c.post("/api/admin/countries", json={"iso2": "XX", "iso3": "XXX", "name": "x"}).status_code == 401

        aid = c.post("/api/applicants", json={"profile": PROFILE, "consent": {"nationality": "advisor"}}).json()["id"]
        res = c.get(f"/api/applicants/{aid}/assessment?country=CA").json()
        assert all(r["definitive"] is False for r in res)             # unverified rules => no definitive claim
        assert not any(r["status"] == "POTENTIALLY ELIGIBLE" for r in res)
        # human verifies rules; now definitive
        c.post("/api/admin/pathways/ca-skilled-economic/verify", headers=H)
        r = [x for x in c.get(f"/api/applicants/{aid}/assessment?country=CA").json() if x["pathway_id"] == "ca-skilled-economic"][0]
        assert r["definitive"] and r["status"] in ("POTENTIALLY ELIGIBLE", "ADDITIONAL REQUIREMENTS NEEDED")
        # advisor respects consent: dob not shared => no age evaluation info leaks
        adv = c.post(f"/api/applicants/{aid}/advisor", json={"mode": "eligibility", "country": "CA"}).json()
        sk = [a for a in adv["assessments"] if a["pathway_id"] == "ca-skilled-economic"][0]
        age_row = [q for q in sk["requirements"] if q["requirement"].startswith("Age")]
        assert age_row and age_row[0]["result"] == "unknown"


def test_verification_and_opportunities():
    with TestClient(app) as c:
        o = c.post("/api/admin/organizations", headers=H, json={
            "name": "FastCash Visas", "category": "recruiter", "country": "CA", "website": "https://fastcashvisas.example",
            "details": {"email": "fastcash@gmail.com", "guarantees_visa": True, "applicant_fee": True}}).json()
        out = c.post(f"/api/admin/organizations/{o['id']}/evaluate", headers=H).json()["outcome"]
        assert out["status"] == "SUSPICIOUS" and out["needs_human_review"]
        good = c.post("/api/admin/organizations", headers=H, json={
            "name": "Acme Care", "category": "employer", "country": "NZ", "website": "https://acme.example",
            "details": {"email": "hr@acme.example", "claims_sponsorship": True}}).json()
        for t in ["government_registry", "job_ad_on_official_site"]:
            c.post(f"/api/admin/organizations/{good['id']}/evidence", headers=H, json={"type": t, "url": "https://x"})
        assert c.post(f"/api/admin/organizations/{good['id']}/evaluate", headers=H).json()["outcome"]["status"] == "PARTIALLY VERIFIED"
        c.post(f"/api/admin/organizations/{good['id']}/evidence", headers=H, json={"type": "domain_whois_match"})
        assert c.post(f"/api/admin/organizations/{good['id']}/evaluate", headers=H).json()["outcome"]["status"] == "VERIFIED"
        opp = {"title": "Care assistant", "org_id": good["id"], "country": "NZ", "kind": "employer_sponsored", "sponsorship": True,
               "applicant_fee": False, "nationality_excluded": ["ZZ"], "deadline": date.today().isoformat()}
        c.post("/api/admin/opportunities", headers=H, json=opp)
        c.post("/api/admin/opportunities", headers=H, json={**opp, "org_id": o["id"], "title": "scam job"})
        got = c.get("/api/opportunities?country=NZ&nationality=LR&no_applicant_fee=true").json()
        assert [x["title"] for x in got] == ["Care assistant"]          # unverified/suspicious hidden by default
        assert c.get("/api/opportunities?nationality=ZZ").json() == []   # nationality exclusion honoured
