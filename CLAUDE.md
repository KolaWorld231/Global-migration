# Project memory: Global Pathways (read first each session)

Migration intelligence + advisory platform. Master brief = the user's 35-section prompt (country-independent, evidence-based,
verification-first, never promise visas, never invent employers/fees/rules, never submit for applicants).

## Stack decision (documented)
Brief suggests Next.js/TS/Postgres. Repo started empty; I built **Python FastAPI + SQLAlchemy (SQLite dev, Postgres via DATABASE_URL) + vanilla JS UI**
to get a tested, working core fast. Migrating UI to Next.js and adding Alembic migrations are open tasks (TODO.md). Do not rewrite working code without reason.

## Layout
app/db.py models · app/security.py auth/RBAC/audit · app/vault.py encrypted private docs · app/prep.py checklist+QA+status flow
app/engine/{eligibility,verification,discovery,advisor,matching}.py · app/seed/countries.json (country data, NOT code) · static/index.html · tests/

## Non-negotiable rules encoded
- Countries/pathways/rules are data; new country = admin API only (tested in test_core).
- Pathway rules with no `last_verified` or >12 months => results are preliminary, never definitive.
- Org VERIFIED needs authoritative evidence + >=2 supporting + no red flags; DISQUALIFIED/INACTIVE/EXPIRED are human-only.
- super_admin has NO document.read; staff doc access = role perm AND applicant consent `documents_staff: share`. Non-owners get 404.
- Advisor reads only consent-shared profile fields. Seeded rules are unverified drafts; no fees/orgs/jobs seeded.
- Uploads: extension+magic-byte+size checks, Fernet-encrypted at rest, no public URLs, audited. Malware scan NOT implemented (reports NOT_SCANNED).

## Status vocabulary
Completed / Partial / Awaiting integration / Awaiting human review / Not implemented — see TODO.md for the live checklist.

## Run
`BOOTSTRAP_ADMIN_EMAIL=.. BOOTSTRAP_ADMIN_PASSWORD=.. uvicorn app.main:app` · `pytest -q` (10 tests passing at last session). SCHEDULER=1 starts the in-process research scheduler.
Env vars: DATABASE_URL, STORAGE_DIR, DOC_ENCRYPTION_KEY (required in prod), BOOTSTRAP_ADMIN_*.
