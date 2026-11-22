# Project memory: Global Pathways (read first each session)

Migration intelligence + advisory platform. Master brief = the user's 35-section prompt (country-independent, evidence-based,
verification-first, never promise visas, never invent employers/fees/rules, never submit for applicants).

## Stack decision (documented)
Brief suggests Next.js/TS/Postgres. Repo started empty; I built **Python FastAPI + SQLAlchemy (SQLite dev, Postgres via DATABASE_URL) + vanilla JS UI**
to get a tested, working core fast. Migrating UI to Next.js is an open task; migrations (Alembic) are done on SQLite only, not yet run on Postgres. Do not rewrite working code without reason.

## Layout
app/db.py models · app/security.py auth/RBAC/audit · app/vault.py encrypted private docs · app/prep.py checklist+QA+status flow
app/agents/ (AI staff: tools, runtime, providers, seed; see AI_SYSTEM_GUIDE.md) · app/workflows.py (data-driven automation) · app/notify.py · app/engine/{eligibility,verification,discovery,advisor,matching}.py · app/seed/countries.json (country data, NOT code) · static/index.html · tests/

## Non-negotiable rules encoded
- Countries/pathways/rules are data; new country = admin API only (tested in test_core).
- Pathway rules with no `last_verified` or >12 months => results are preliminary, never definitive.
- Org VERIFIED needs authoritative evidence + >=2 supporting + no red flags; DISQUALIFIED/INACTIVE/EXPIRED are human-only.
- super_admin has NO document.read; staff doc access = role perm AND applicant consent `documents_staff: share`. Non-owners get 404.
- Advisor reads only consent-shared profile fields. Seeded rules are unverified drafts; no fees/orgs/jobs seeded.
- Uploads: extension+magic-byte+size checks, Fernet-encrypted at rest, no public URLs, audited. Malware scan NOT implemented (reports NOT_SCANNED).

- AI agents: LOW tools run, HIGH tools are proposals needing a human with the tool's permission; agents never send/submit/read documents. Do not widen this without the owner's say-so.

## Status vocabulary
Completed / Partial / Awaiting integration / Awaiting human review / Not implemented — see TODO.md for the live checklist.

Schema is managed by Alembic (migrations/). After editing app/db.py run `alembic revision --autogenerate -m "msg"` (test_migrations_match_models fails otherwise). Startup applies `upgrade head`. Dev DBs created before migrations existed must be deleted once.

## Run
`BOOTSTRAP_ADMIN_EMAIL=.. BOOTSTRAP_ADMIN_PASSWORD=.. uvicorn app.main:app` · `pytest -q` (24 tests passing at last session; CI in .github/workflows/ci.yml). SCHEDULER=1 starts the in-process research scheduler.
Env vars: DATABASE_URL, STORAGE_DIR, DOC_ENCRYPTION_KEY (required in prod), BOOTSTRAP_ADMIN_*.
