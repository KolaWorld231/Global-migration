# Global Pathways

Country-agnostic migration intelligence platform (FastAPI + SQLAlchemy + vanilla JS).

## Run
    pip install -r requirements.txt
    BOOTSTRAP_ADMIN_EMAIL=you@example.org BOOTSTRAP_ADMIN_PASSWORD="ChangeMe12345" uvicorn app.main:app --reload     # http://localhost:8000
    pytest

## Design
- **Countries are data.** `app/seed/countries.json` seeds AU, CA, VN, NO, NZ (destinations) and LR + other origins.
  New countries: `POST /api/admin/countries` -> `POST /api/admin/pathways` -> `POST /api/admin/countries/{iso}/activate`. No code changes.
- **Rules are data.** Pathway `requirements` are declarative (`field`, `op`, `value`, `kind`, `blocking`); `app/engine/eligibility.py` is generic.
  Rules with no `last_verified` (or >12 months old) yield preliminary results only, never a definitive conclusion.
- **Verification** (`app/engine/verification.py`): evidence-based; VERIFIED needs authoritative evidence + supporting evidence + no red flags.
  Suspicious cases go to the human review queue; DISQUALIFIED/INACTIVE/EXPIRED are human decisions only.
- **Opportunities** inherit no more verification than their organization; unverified listings are hidden by default.
- **Privacy**: profile fields are private unless the applicant marks them shared with the advisor; no passport/medical numbers collected.
- **Discovery** (`app/engine/discovery.py`): adapter framework with robots.txt checks and rate limiting; candidates enter UNVERIFIED.

## Important
The seeded pathway requirements are **draft placeholders, unverified**. A person must check each against the official source
and call `POST /api/admin/pathways/{id}/verify`. No fees, organizations or job listings are seeded; none are invented.
