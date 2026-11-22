"""Research pipeline: fetch permitted sources -> snapshot -> detect change / extract candidate leads -> store.
All fetching goes through discovery.polite_get (robots.txt, rate limit). Nothing is published automatically."""
import hashlib, re, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from ..db import (SessionLocal, ResearchSource, SourceSnapshot, RegulatoryChange, ResearchResult, ResearchRun)
from . import discovery

MAX_ATTEMPTS = 3


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def parse_feed(xml: str):
    root = ET.fromstring(xml)
    for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        def g(tag):
            e = it.find(tag) if it.find(tag) is not None else it.find("{http://www.w3.org/2005/Atom}" + tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        link = g("link")
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href", "") if le is not None else ""
        yield {"title": g("title"), "url": link, "published": g("pubDate") or g("published") or g("updated"),
               "excerpt": visible_text(g("description") or g("summary"))[:500]}


def run_source(s, src, fetch=None):
    """Execute one fetch. Returns summary dict; raises on failure."""
    fetch = fetch or (lambda u: discovery.polite_get(u))
    r = fetch(src.url)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    out = {"new_leads": 0, "duplicates": 0, "changed": False}
    if src.kind == "feed":
        for item in parse_feed(r.text):
            if not item["url"]:
                continue
            h = hashlib.sha256(item["url"].encode()).hexdigest()
            if s.query(ResearchResult).filter_by(url_hash=h).first():
                out["duplicates"] += 1
                continue
            s.add(ResearchResult(source_id=src.id, country=src.country, url_hash=h, source_level=src.level, **item))
            out["new_leads"] += 1
    else:
        text = visible_text(r.text)
        sha = hashlib.sha256(text.encode()).hexdigest()
        prev = s.query(SourceSnapshot).filter_by(source_id=src.id).order_by(SourceSnapshot.id.desc()).first()
        if not prev or prev.sha256 != sha:
            snap = SourceSnapshot(source_id=src.id, sha256=sha, text=text[:200000]); s.add(snap); s.flush()
            if prev:  # first snapshot is a baseline, not a change
                out["changed"] = True
                s.add(RegulatoryChange(source_id=src.id, country=src.country, policy_area=src.policy_area,
                                       previous_snapshot=prev.id, new_snapshot=snap.id,
                                       summary=f"Content of {src.url} changed; a human must compare snapshots {prev.id} and {snap.id}."))
    src.last_ok = datetime.utcnow()
    return out


def execute(src_id, fetch=None):
    """Run with retry bookkeeping. Failed runs stay retryable until MAX_ATTEMPTS, then become DEAD (dead-letter)."""
    with SessionLocal() as s:
        src = s.get(ResearchSource, src_id)
        run = ResearchRun(source_id=src_id); s.add(run); s.flush()
        src.last_run = datetime.utcnow()
        try:
            run.attempts = 1
            run.summary = run_source(s, src, fetch); run.status = "OK"
        except Exception as e:
            s.rollback()
            run = s.get(ResearchRun, run.id) or ResearchRun(source_id=src_id)
            run.status, run.error, run.attempts = "FAILED", str(e)[:500], 1
            s.add(run)
        s.commit()
        return run.id


def retry(run_id, fetch=None):
    with SessionLocal() as s:
        run = s.get(ResearchRun, run_id)
        if not run or run.status not in ("FAILED",):
            return None
        src = s.get(ResearchSource, run.source_id)
        n = run.attempts + 1
        try:
            run.attempts = n
            run.summary = run_source(s, src, fetch); run.status, run.error = "OK", ""
        except Exception as e:
            s.rollback(); run = s.get(ResearchRun, run_id)
            run.attempts, run.error = n, str(e)[:500]
            run.status = "DEAD" if n >= MAX_ATTEMPTS else "FAILED"
        s.commit()
        return run.status


def due_sources(s, now=None):
    now = now or datetime.utcnow()
    return [x for x in s.query(ResearchSource).filter_by(active=True)
            if x.last_run is None or now - x.last_run >= timedelta(hours=x.interval_hours)]


def run_due(fetch=None):
    with SessionLocal() as s:
        ids = [x.id for x in due_sources(s)]
    return [execute(i, fetch) for i in ids]
