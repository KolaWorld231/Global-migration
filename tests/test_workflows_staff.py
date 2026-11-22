import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t5.db")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "root@example.org")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "RootPassw0rdX")
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app import workflows
from app.db import SessionLocal, WorkflowRun
from tests.test_core import tok, applicant
from tests.test_cases_notify import staff


def wf(c, H, **kw):
    return c.post("/api/admin/workflows", headers=H, json=kw)


def test_default_onboarding_workflow_runs():
    with TestClient(app) as c:
        root = tok(c)
        adv = staff(c, root, "wfadv@x.org", "applicant_advisor")
        a, A = applicant(c, "wf1@example.org")
        assert any(n["title"].startswith("Welcome") for n in c.get("/api/notifications", headers=A).json())
        case = [x for x in c.get("/api/cases", headers=adv).json() if x["applicant_id"] == a][0]
        assert case["assigned_to"] is not None                                      # auto-assigned, never to super_admin
        assert c.get("/api/auth/me", headers=root).json()["id"] != case["assigned_to"]
        assert c.get("/api/admin/workflows", headers=adv).status_code == 403


def test_validation_conditions_delay_approval_and_failures():
    with TestClient(app) as c:
        root = tok(c)
        assert wf(c, root, name="bad", trigger="nope", steps=[{"type": "notify", "title": "x"}]).status_code == 422
        assert wf(c, root, name="bad2", trigger="fraud.reported", steps=[{"type": "exec", "cmd": "rm"}]).status_code == 422
        assert wf(c, root, name="bad3", trigger="fraud.reported", steps=[{"type": "notify"}]).status_code == 422
        # condition + delay + approval + task, authored without code changes
        assert wf(c, root, name="submitted follow-up", trigger="application.status_changed",
                  conditions=[{"field": "status", "op": "eq", "value": "SUBMITTED"}],
                  steps=[{"type": "delay", "hours": 24}, {"type": "approval", "perm": "case.manage"},
                         {"type": "create_task", "title": "Chase {pathway_id}", "due_in_days": 1}]).status_code == 200
        a, A = applicant(c, "wf2@example.org")
        c.put(f"/api/applicants/{a}", json={"profile": {"full_name": "W T", "dob": "1990-01-01", "nationality": "LR"}}, headers=A)
        app_id = c.post("/api/applications", headers=A, json={"pathway_id": "nz-student"}).json()["id"]
        c.post(f"/api/applications/{app_id}/status", headers=A, json={"status": "READY"})   # blocked: ignored
        with SessionLocal() as s:
            assert s.query(WorkflowRun).filter_by(status="WAITING_DELAY").count() == 0       # condition not met
            from app.db import Application
            ap = s.get(Application, app_id); ap.status = "READY"; s.commit()
        r = c.post(f"/api/applications/{app_id}/status", headers=A, json={"status": "SUBMITTED"})
        assert r.status_code == 200
        runs = c.get("/api/admin/workflow-runs?status=WAITING_DELAY", headers=root).json()
        run = [x for x in runs if x["context"].get("application_id") == app_id][0]
        assert c.post("/api/admin/workflow-runs/tick", headers=root).json()["resumed"] == 0   # not due yet
        with SessionLocal() as s:
            x = s.get(WorkflowRun, run["id"]); x.resume_at = datetime.utcnow() - timedelta(minutes=1); s.commit()
        assert c.post("/api/admin/workflow-runs/tick", headers=root).json()["resumed"] >= 1
        assert c.get("/api/admin/workflow-runs?status=WAITING_APPROVAL", headers=root).json()
        fin = staff(c, root, "fin@x.org", "finance_officer")
        assert c.post(f"/api/admin/workflow-runs/{run['id']}/decide?approve=true", headers=fin).status_code == 403
        adv = staff(c, root, "wfadv2@x.org", "applicant_advisor")
        done = c.post(f"/api/admin/workflow-runs/{run['id']}/decide?approve=true", headers=adv).json()
        assert done["status"] == "DONE" and [l["step"] for l in done["log"]] == ["delay", "approval", "approval", "create_task"]
        case = [x for x in c.get("/api/cases", headers=adv).json() if x["applicant_id"] == a][0]
        assert any("Chase nz-student" in t["title"] for t in c.get(f"/api/cases/{case['id']}", headers=adv).json()["tasks"])


def test_failure_is_logged_and_retryable_and_rejection():
    with TestClient(app) as c:
        root = tok(c)
        wf(c, root, name="needs staff", trigger="fraud.reported",
           steps=[{"type": "assign_case", "staff_id": 999999}])
        wf(c, root, name="approve me", trigger="fraud.reported", conditions=[{"field": "category", "op": "eq", "value": "financial_scam"}],
           steps=[{"type": "approval"}, {"type": "escalate", "staff_id": 1, "message": "x"}])
        a, A = applicant(c, "wf3@example.org")
        c.post("/api/fraud/reports", headers=A, json={"subject": "s", "category": "financial_scam", "description": "d"})
        failed = c.get("/api/admin/workflow-runs?status=FAILED", headers=root).json()
        assert failed and failed[0]["error"].startswith("step 0 (assign_case)")
        r = c.post(f"/api/admin/workflow-runs/{failed[0]['id']}/retry", headers=root).json()
        assert r["status"] == "FAILED"                                           # still failing, honestly reported
        wait = c.get("/api/admin/workflow-runs?status=WAITING_APPROVAL", headers=root).json()[0]
        assert c.post(f"/api/admin/workflow-runs/{wait['id']}/decide?approve=false", headers=root).json()["status"] == "REJECTED"


def test_staff_management_guards():
    with TestClient(app) as c:
        root = tok(c)
        s1 = staff(c, root, "m1@x.org", "support_officer")
        lst = c.get("/api/admin/staff", headers=root).json()
        me = c.get("/api/auth/me", headers=root).json()["id"]
        uid = [x for x in lst if x["email"] == "m1@x.org"][0]["id"]
        assert c.patch(f"/api/admin/staff/{me}", headers=root, json={"active": False}).status_code == 422     # no self-lockout
        assert c.patch(f"/api/admin/staff/{uid}", headers=root, json={"role": "bogus"}).status_code == 422
        assert c.get("/api/auth/me", headers=s1).status_code == 200
        assert c.patch(f"/api/admin/staff/{uid}", headers=root, json={"active": False}).status_code == 200
        assert c.get("/api/auth/me", headers=s1).status_code == 401                                         # sessions revoked
        assert c.patch(f"/api/admin/staff/{uid}", headers=s1, json={"role": "super_admin"}).status_code == 401
        # a second super admin may demote the first only because another remains
        c.post("/api/admin/staff", headers=root, json={"email": "root2@x.org", "password": "RootTwoPass123", "role": "super_admin"})
        r2 = tok(c, "root2@x.org", "RootTwoPass123")
        assert c.patch(f"/api/admin/staff/{me}", headers=r2, json={"role": "agency_director"}).status_code == 200
        assert c.patch(f"/api/admin/staff/{c.get('/api/auth/me', headers=r2).json()['id']}", headers=r2, json={"active": False}).status_code == 422
