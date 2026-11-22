# Implementation checklist (update every session)
Legend: [x] completed+tested · [~] partial · [i] awaiting external integration · [h] awaiting human review · [ ] not implemented

## Strategic priorities (added by product owner)
Order: 1) verification+research engine and country foundation (in progress) → 2) matching → 3) readiness scoring → 4) partner network → 5) employer CRM → 6) outcome intelligence
- [~] Applicant opportunity matching: explainable matcher exists (engine/matching.py); needs occupation/language/salary criteria, saved opportunities, notifications
- [~] Migration readiness scoring: per-pathway readiness % + gap actions exist; needs improvement-plan ("what to fix first") and cross-pathway view
- [ ] Verified partner network: licensed-practitioner directory with licence-register evidence + referral flow (compliance §19)
- [ ] Employer relationship management: employer records, outreach tracking, draft-only outreach assistant (no auto-send)
- [ ] Outcome intelligence: voluntary outcome stages (discovered→prepared→submitted→response→offer→permit→visa→relocated); aggregate with small-sample warnings

## Phase 1 Foundation
- [x] Repo, modular FastAPI app, config-driven models, tests
- [x] Auth (scrypt, sessions, rate-limited login), 11 roles, RBAC, audit log, tenant column on users
- [~] Multi-tenancy: tenant on users only; add to all tables
- [ ] Alembic migrations · MFA · email verification · password recovery · CSRF (token auth in header, so CSRF n/a unless cookies adopted)
## Phase 2 Country intelligence
- [x] 5 destinations + 8 origin markets as data; admin add/activate/verify country & pathways; expansion test
- [h] All seeded pathway requirements are DRAFT: each needs checking against official sources
- [ ] Regulatory change records with version history; source snapshots; policy-source monitoring
- [ ] Country-specific nationality/country-of-application restrictions research
## Phase 3 Organizations/opportunities
- [x] Verification engine, evidence, human decision, review queue, opportunity inheritance, filters
- [~] Discovery framework (robots.txt, rate limit); only a source-reachability adapter. [ ] real adapters, dedupe, scheduler, retry/dead-letter
- [ ] Organization contacts/recruitment portal/benefit fields as first-class columns (currently in details JSON)
## Phase 4 Applicant experience
- [x] Register/login, profile with per-field consent, multi-country assessment, advisor (rule-based), matches
- [ ] AI (Claude) explanation layer [i needs API key] · full profile schema (education/employment history tables)
## Phase 5 Application operations
- [x] Encrypted private document vault, applications, checklist, QA, status flow, applicant authorisation, export & deletion
- [i] Malware scanning (ClamAV adapter) · [ ] AI document extraction · CV/letter drafting · [ ] staff case management (assign, tasks, notes, consultations)
## Phase 6-8 Automation, notifications, analytics, billing, i18n, workflow builder, fraud module, compliance pages, admin dashboard modules
- [ ] Not started (fraud reporting/investigation is high priority next after verification work)
