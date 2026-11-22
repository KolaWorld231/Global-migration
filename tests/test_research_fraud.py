import os, tempfile
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t3.db")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "root@example.org")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "RootPassw0rdX")
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())
from fastapi.testclient import TestClient
from app.main import app
from app.engine import research
from app.db import SessionLocal, ResearchRun, ResearchSource
from tests.test_core import tok, applicant

RSS = """<rss><channel><item><title>New skilled visa list</title><link>https://gov.example/a</link><pubDate>Mon</pubDate><description>&lt;p&gt;hello&lt;/p&gt;</description></item>
<item><title>Scholarship</title><link>https://gov.example/b</link></item></channel></rss>"""


class R:
    def __init__(self, text, code=200): self.text, self.status_code = text, code


def src(c, H, url, kind="page"):
    return c.post("/api/admin/research/sources", headers=H, json={"country": "CA", "title": "t", "url": url, "kind": kind, "policy_area": "fees"}).json()["id"]


def test_feed_dedupe_and_page_change_history():
    with TestClient(app) as c:
        H = tok(c)
        fid = src(c, H, "https://gov.example/feed.xml", "feed")
        research.execute(fid, fetch=lambda u: R(RSS))
        research.execute(fid, fetch=lambda u: R(RSS))                       # same items again => duplicates, not new
        res = c.get("/api/admin/research/results", headers=H).json()
        assert len(res) == 2 and all(r["status"] == "NEEDS_REVIEW" for r in res)  # never auto-published
        pid = src(c, H, "https://gov.example/fees")
        research.execute(pid, fetch=lambda u: R("<html><body>Fee 100</body></html>"))
        assert c.get("/api/admin/regulatory-changes", headers=H).json() == []         # baseline only
        research.execute(pid, fetch=lambda u: R("<html><body>Fee 150</body></html>"))
        ch = c.get("/api/admin/regulatory-changes", headers=H).json()
        assert len(ch) == 1 and ch[0]["previous_snapshot"] != ch[0]["new_snapshot"]
        old = c.get(f"/api/admin/snapshots/{ch[0]['previous_snapshot']}", headers=H).json()
        assert "Fee 100" in old["text"]                                                # history preserved
        c.post("/api/admin/pathways/ca-study/verify", headers=H)
        c.post(f"/api/admin/regulatory-changes/{ch[0]['id']}/review?decision=CONFIRMED_IMPACT", headers=H)
        p = [x for x in c.get("/api/pathways?country=CA").json() if x["id"] == "ca-study"][0]
        assert p["last_verified"] is None                                              # forced re-verification


def test_retry_and_dead_letter():
    with TestClient(app) as c:
        H = tok(c)
        sid = src(c, H, "https://gov.example/down")
        def boom(u): raise RuntimeError("blocked")
        rid = research.execute(sid, fetch=boom)
        with SessionLocal() as s: assert s.get(ResearchRun, rid).status == "FAILED"
        assert research.retry(rid, fetch=boom) == "FAILED"
        assert research.retry(rid, fetch=boom) == "DEAD"
        assert research.retry(rid, fetch=boom) is None
        rid2 = research.execute(sid, fetch=boom)
        assert research.retry(rid2, fetch=lambda u: R("<p>ok</p>")) == "OK"


def test_due_sources_and_unreachable_robots():
    with TestClient(app) as c:
        H = tok(c)
        sid = src(c, H, "https://gov.example/due")
        with SessionLocal() as s: assert sid in [x.id for x in research.due_sources(s)]
        research.execute(sid, fetch=lambda u: R("x"))
        with SessionLocal() as s: assert sid not in [x.id for x in research.due_sources(s)]
        assert c.post("/api/admin/research/sources", headers=H, json={"country": "CA", "title": "t", "url": "http://insecure", "kind": "page"}).status_code == 422


def test_fraud_check_reports_and_workflow():
    with TestClient(app) as c:
        out = c.post("/api/fraud/check", json={"text": "We GUARANTEE a visa! Pay a deposit for your job offer to my personal account via Western Union. Act now"}).json()
        kinds = {i["indicator"] for i in out["indicators"]}
        assert {"guaranteed_visa", "pay_for_job", "personal_account", "pressure"} <= kinds
        assert c.post("/api/fraud/check", json={"text": "Please apply on our careers page."}).json()["indicators"] == []
        H = tok(c)
        o = c.post("/api/admin/organizations", headers=H, json={"name": "Shady", "category": "recruiter", "country": "AU"}).json()
        _, U = applicant(c, "rep@example.org")
        r = c.post("/api/fraud/reports", headers=U, json={"subject": "Shady", "category": "financial_scam", "description": "asked me to pay to personal account", "org_id": o["id"]}).json()
        org = [x for x in c.get("/api/organizations").json() if x["id"] == o["id"]][0]
        assert org["status"] == "UNVERIFIED" and org["needs_human_review"]            # not publicly labelled
        assert c.get("/api/admin/fraud/reports", headers=U).status_code == 403
        assert c.post(f"/api/admin/fraud/reports/{r['id']}", headers=H, json={"status": "CONFIRMED"}).status_code == 422  # needs evidence
        c.post(f"/api/admin/fraud/reports/{r['id']}", headers=H, json={"status": "CONFIRMED", "note": "bank details match known scam"})
        org = [x for x in c.get("/api/organizations").json() if x["id"] == o["id"]][0]
        assert org["status"] == "DISQUALIFIED"
