import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t2.db")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "root@example.org")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "RootPassw0rdX")
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())
from datetime import date, timedelta
from fastapi.testclient import TestClient
from app.main import app
from tests.test_core import tok, applicant

PDF = b"%PDF-1.4 test"


def test_isolation_and_roles():
    with TestClient(app) as c:
        a1, H1 = applicant(c, "u1@example.org"); a2, H2 = applicant(c, "u2@example.org")
        c.put(f"/api/applicants/{a1}", json={"profile": {"full_name": "A One"}}, headers=H1)
        assert c.get(f"/api/applicants/{a1}", headers=H2).status_code == 404       # cross-user
        assert c.get(f"/api/applicants/{a1}").status_code == 401                     # anonymous
        assert c.post("/api/admin/countries", headers=H1, json={"iso2": "ZZ", "iso3": "ZZZ", "name": "z"}).status_code == 403
        root = tok(c)
        assert c.post("/api/admin/staff", headers=root, json={"email": "dr@x.org", "password": "DocReview123", "role": "document_reviewer"}).status_code == 200
        assert c.post("/api/admin/staff", headers=root, json={"email": "ad@x.org", "password": "AdvisorPass12", "role": "applicant_advisor"}).status_code == 200
        r = c.post(f"/api/applicants/{a1}/documents", headers=H1, data={"doc_type": "passport"}, files={"file": ("p.pdf", PDF)})
        did = r.json()["id"]
        assert c.get(f"/api/documents/{did}/content", headers=H1).content == PDF
        assert c.get(f"/api/documents/{did}/content", headers=H2).status_code == 404
        assert c.get(f"/api/documents/{did}/content", headers=root).status_code == 404          # super admin: no doc access
        DR = tok(c, "dr@x.org", "DocReview123")
        assert c.get(f"/api/documents/{did}/content", headers=DR).status_code == 404            # role ok, no consent
        c.put(f"/api/applicants/{a1}", json={"consent": {"documents_staff": "share"}}, headers=H1)
        assert c.get(f"/api/documents/{did}/content", headers=DR).status_code == 200            # role + consent
        AD = tok(c, "ad@x.org", "AdvisorPass12")
        assert c.get(f"/api/documents/{did}/content", headers=AD).status_code == 404            # advisor lacks document.read
        actions = [a["action"] for a in c.get("/api/admin/audit?limit=200", headers=root).json()]
        assert "document_read" in actions and "document_denied" in actions and "permission_denied" in actions


def test_upload_validation_and_login_limits():
    with TestClient(app) as c:
        a, H = applicant(c, "u3@example.org")
        up = lambda name, data: c.post(f"/api/applicants/{a}/documents", headers=H, data={"doc_type": "cv"}, files={"file": (name, data)})
        assert up("x.exe", b"MZ").status_code == 422
        assert up("fake.pdf", b"not a pdf").status_code == 422
        assert up("../../evil.pdf", PDF).json()["filename"] == "evil.pdf"
        for _ in range(5):
            c.post("/api/auth/login", json={"email": "u3@example.org", "password": "wrong"})
        assert c.post("/api/auth/login", json={"email": "u3@example.org", "password": "Applicant123x"}).status_code == 429
        assert c.post("/api/auth/register", json={"email": "weak@example.org", "password": "short"}).status_code == 422


def test_application_flow_and_delete():
    with TestClient(app) as c:
        a, H = applicant(c, "u4@example.org")
        c.put(f"/api/applicants/{a}", json={"profile": {"full_name": "Ann Tester", "dob": "1990-01-01", "nationality": "LR"}}, headers=H)
        app_id = c.post("/api/applications", headers=H, json={"pathway_id": "nz-student"}).json()["id"]
        qa = c.get(f"/api/applications/{app_id}/quality", headers=H).json()
        assert not qa["ready"] and any("Missing document" in i["message"] for i in qa["issues"])
        assert c.post(f"/api/applications/{app_id}/status", headers=H, json={"status": "READY"}).status_code == 422
        assert c.post(f"/api/applications/{app_id}/status", headers=H, json={"status": "SUBMITTED"}).status_code == 422  # skipping stages
        cl = c.get(f"/api/applications/{app_id}/checklist", headers=H).json()["checklist"]
        for item in cl:
            if item["status"] == "MISSING":
                c.post(f"/api/applicants/{a}/documents", headers=H, data={"doc_type": item["doc_type"], "expires_on": (date.today() + timedelta(days=900)).isoformat()}, files={"file": ("d.pdf", PDF)})
        c.post(f"/api/applications/{app_id}/authorize", headers=H)
        qa = c.get(f"/api/applications/{app_id}/quality", headers=H).json()
        assert qa["ready"], qa
        assert c.post(f"/api/applications/{app_id}/status", headers=H, json={"status": "READY"}).status_code == 200
        r = c.post(f"/api/applications/{app_id}/status", headers=H, json={"status": "SUBMITTED", "reference": "REF1"}).json()
        assert r["submitted_on"] and r["reference"] == "REF1"
        assert c.get(f"/api/applicants/{a}/export", headers=H).status_code == 200
        assert c.delete(f"/api/applicants/{a}", headers=H).status_code == 200
        assert c.get(f"/api/applicants/{a}", headers=H).status_code == 401   # account gone


def test_matching_explainable():
    with TestClient(app) as c:
        H = tok(c)
        o = c.post("/api/admin/organizations", headers=H, json={"name": "Org", "category": "employer", "country": "NO", "website": "https://o.example"}).json()
        for t in ["government_registry", "domain_whois_match", "physical_presence"]:
            c.post(f"/api/admin/organizations/{o['id']}/evidence", headers=H, json={"type": t})
        c.post(f"/api/admin/organizations/{o['id']}/evaluate", headers=H)
        c.post("/api/admin/opportunities", headers=H, json={"title": "Welder", "org_id": o["id"], "country": "NO", "kind": "employer_sponsored", "min_education": "vocational", "min_experience_years": 2})
        a, A = applicant(c, "u5@example.org")
        c.put(f"/api/applicants/{a}", json={"profile": {"nationality": "LR", "education": {"level": "secondary"}, "employment": {"total_years": 5}}}, headers=A)
        m = c.get(f"/api/applicants/{a}/matches", headers=A).json()
        w = [x for x in m if x["title"] == "Welder"][0]
        assert "education >= vocational" in w["gaps"] and "experience >= 2.0y" in w["met"]


def test_migrations_match_models():
    """Fails when someone changes app/db.py without adding a migration."""
    import pathlib
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.db import Base, engine
    with TestClient(app):  # runs startup => migrations
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
