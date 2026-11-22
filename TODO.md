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
- [x] Alembic migrations (SQLite-verified; drift test) + CI workflow (not yet observed running on GitHub)
- [ ] Verify migrations on PostgreSQL · MFA · email verification · password recovery · CSRF (token auth in header, so CSRF n/a unless cookies adopted)
## Phase 2 Country intelligence
- [x] 5 destinations + 8 origin markets as data; admin add/activate/verify country & pathways; expansion test
- [h] All seeded pathway requirements are DRAFT: each needs checking against official sources
- [~] Regulatory changes + immutable snapshots + review (CONFIRMED_IMPACT forces pathway re-verification); [ ] structured previous/new values, effective dates, affected-applicant identification, notifications
- [ ] Country-specific nationality/country-of-application restrictions research
## Phase 3 Organizations/opportunities
- [x] Verification engine, evidence, human decision, review queue, opportunity inheritance, filters
- [x] Research pipeline: source registry (hierarchy levels), page snapshots + change detection -> regulatory change review, RSS/Atom feed leads with URL dedupe, retry/dead-letter, due-source scheduler (SCHEDULER=1), all via robots-aware fetcher. Tested with mocked fetch only; NOT yet exercised against live sites
- [i] Source-specific adapters (employer career portals, scholarship portals, licence registers) and AI extraction of opportunities from leads: [ ] not implemented
- [ ] Organization/opportunity dedupe (lead dedupe by URL only)
- [ ] Organization contacts/recruitment portal/benefit fields as first-class columns (currently in details JSON)
## Phase 4 Applicant experience
- [x] Register/login, applicant UI (account, profile, assessment, matches, applications+uploads, advisor, alerts, safety), profile with per-field consent, multi-country assessment, advisor (rule-based), matches
- [ ] AI (Claude) explanation layer [i needs API key] · full profile schema (education/employment history tables)
## Phase 5 Application operations
- [x] Encrypted private document vault, applications, checklist, QA, status flow, applicant authorisation, export & deletion
- [i] Malware scanning (ClamAV adapter) · [ ] AI document extraction · CV/letter drafting
- [x] Staff case management: one case per applicant, 18 configurable stages, assign/reassign (staff-only assignees), notes (internal), tasks, communication log, dashboard (doc metadata only), time-per-stage report, applicant sees stage only
- [ ] Consultation scheduling (Google Calendar adapter) · case report export · task reminders
## Phase 6-8 Automation, notifications, analytics, billing, i18n, workflow builder, fraud module, compliance pages, admin dashboard modules
- [~] Notifications: in-app + preference model + critical/non-essential split + dedupe + deadline/document/regulatory triggers done and tested. [i] Email/SMS/WhatsApp/push providers (adapter interface only; email_status honestly reports NOT_CONFIGURED); [ ] matching/verification-change triggers, unsubscribe links
- [x] Admin dashboard API (real aggregates, health incl. honest NOT_CONFIGURED flags, small-sample cautions) + staff UI (cases, fraud triage, regulatory review, lead review, research runs, audit). Browser smoke-tested (Playwright, phone width)
- [ ] Not started: workflow builder, deeper analytics/reports export, billing, i18n, remaining admin modules (staff mgmt UI, integrations, AI config, settings), communication templates/outreach drafts, compliance pages (fraud module done: see below; next: notifications, case management, admin dashboard)
- [x] Fraud: offer text indicator checker (public), user reports (no public labelling), investigation workflow, confirmed => DISQUALIFIED with evidence note. [ ] link-/domain-level checks, report deduplication, reporter protection policy
