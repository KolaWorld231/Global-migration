import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t4.db")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "root@example.org")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "RootPassw0rdX")
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())
from datetime import date, timedelta
from fastapi.testclient import TestClient
from app.main import app
from tests.test_core import tok, applicant

PDF = b"%PDF-1.4 x"


def staff(c, root, email, role, pw="StaffPass1234"):
    c.post("/api/admin/staff", headers=root, json={"email": email, "password": pw, "role": role})
    return tok(c, email, pw)


def test_case_lifecycle_permissions_and_privacy():
    with TestClient(app) as c:
        root = tok(c)
        adv = staff(c, root, "adv@x.org", "applicant_advisor")
        sup = staff(c, root, "sup@x.org", "finance_officer")
        a, A = applicant(c, "cs1@example.org")
        cases = c.get("/api/cases", headers=adv).json()
        mine = [x for x in cases if x["applicant_id"] == a][0]
        assert mine["stage"] == "NEW_APPLICANT"
        assert c.get("/api/cases", headers=A).status_code == 403 and c.get("/api/cases", headers=sup).status_code == 403
        advid = c.get("/api/auth/me", headers=adv).json()["id"]
        assert c.post(f"/api/cases/{mine['id']}/assign", headers=adv, json={"staff_id": advid}).json()["assigned_to"] == advid
        assert c.post(f"/api/cases/{mine['id']}/assign", headers=adv, json={"staff_id": c.get("/api/auth/me", headers=sup).json()["id"]}).status_code == 422
        assert c.post(f"/api/cases/{mine['id']}/stage?stage=BOGUS", headers=adv).status_code == 422
        c.post(f"/api/cases/{mine['id']}/stage?stage=PROFILE_COMPLETED", headers=adv)
        c.post(f"/api/cases/{mine['id']}/notes", headers=adv, json={"body": "internal remark"})
        c.post(f"/api/cases/{mine['id']}/tasks", headers=adv, json={"title": "Call applicant", "due": date.today().isoformat()})
        c.post(f"/api/cases/{mine['id']}/communications", headers=adv, json={"channel": "email", "direction": "outbound", "summary": "sent intro"})
        c.post(f"/api/applicants/{a}/documents", headers=A, data={"doc_type": "passport"}, files={"file": ("p.pdf", PDF)})
        d = c.get(f"/api/cases/{mine['id']}", headers=adv).json()
        assert d["notes"] and d["tasks"] and d["communications"] and d["documents"][0]["doc_type"] == "passport"
        assert "filename" not in d["documents"][0]                        # metadata only, no content access
        assert "internal remark" not in str(c.get("/api/my-case", headers=A).json())
        r = c.get(f"/api/cases/{mine['id']}/report", headers=adv).json()
        assert set(r["days_per_stage"]) == {"NEW_APPLICANT", "PROFILE_COMPLETED"}
        # custom stages
        assert c.put("/api/admin/case-stages", headers=adv, json=["A"]).status_code == 403
        assert c.put("/api/admin/case-stages", headers=root, json=["NEW_APPLICANT"]).status_code == 422  # PROFILE_COMPLETED in use
        new = c.get("/api/case-stages", headers=root).json() + ["CUSTOM_REVIEW"]
        assert c.put("/api/admin/case-stages", headers=root, json=new).status_code == 200
        assert c.post(f"/api/cases/{mine['id']}/stage?stage=CUSTOM_REVIEW", headers=adv).status_code == 200


def test_notifications_prefs_dedupe_and_scan():
    with TestClient(app) as c:
        root = tok(c)
        a, A = applicant(c, "nt1@example.org")
        c.put(f"/api/applicants/{a}", json={"profile": {"full_name": "N T", "dob": "1990-01-01", "nationality": "LR"}}, headers=A)
        aid = c.post("/api/applications", headers=A, json={"pathway_id": "nz-student", "deadline": (date.today() + timedelta(days=5)).isoformat()}).json()["id"]
        c.post(f"/api/applicants/{a}/documents", headers=A, data={"doc_type": "passport", "expires_on": (date.today() + timedelta(days=10)).isoformat()}, files={"file": ("p.pdf", PDF)})
        assert c.post("/api/admin/notifications/scan", headers=A).status_code == 403
        assert c.post("/api/admin/notifications/scan", headers=root).json()["sent"] >= 2
        assert c.post("/api/admin/notifications/scan", headers=root).json()["sent"] == 0       # idempotent
        ns = c.get("/api/notifications", headers=A).json()
        kinds = {n["kind"] for n in ns}
        assert {"deadline", "document_expiry"} <= kinds and all(n["critical"] for n in ns)
        assert all(n["email_status"] == "NOT_CONFIGURED" for n in ns)                           # honest: no provider
        assert c.post(f"/api/notifications/{ns[0]['id']}/read", headers=A).json()["read"]
        # another user cannot read it
        _, B = applicant(c, "nt2@example.org")
        assert c.post(f"/api/notifications/{ns[0]['id']}/read", headers=B).status_code == 404
        # non-essential suppressed unless opted in
        from app.db import SessionLocal
        from app import notify
        with SessionLocal() as s:
            uid = c.get("/api/auth/me", headers=A).json()["id"]
            assert notify.notify(s, uid, "opportunity_match", "New match") is None
            c.put("/api/notification-prefs", headers=A, json={"opportunity_alerts": True})
            assert notify.notify(s, uid, "opportunity_match", "New match") is not None


def test_regulatory_confirmation_notifies_affected_applicants():
    with TestClient(app) as c:
        root = tok(c)
        a, A = applicant(c, "reg1@example.org")
        c.post("/api/applications", headers=A, json={"pathway_id": "ca-study"})
        from app.db import SessionLocal, RegulatoryChange
        with SessionLocal() as s:
            ch = RegulatoryChange(source_id=1, country="CA", policy_area="fees", previous_snapshot=1, new_snapshot=2, summary="x")
            s.add(ch); s.commit(); cid = ch.id
        c.post(f"/api/admin/regulatory-changes/{cid}/review?decision=CONFIRMED_IMPACT", headers=root)
        assert any(n["kind"] == "regulatory_change" for n in c.get("/api/notifications", headers=A).json())
