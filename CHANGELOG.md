# Changelog
## Session 8
- AI agents as staff with human approval boundary; migration 9483d0164674; 24 tests passing; AI_SYSTEM_GUIDE.md.
## Session 7
- Workflow engine, staff management, staff UI panels; 19 tests passing. Migration 486abc618d3d.
## Session 6
- Alembic migrations with drift test; CI workflow. 15 tests passing.
## Session 5
- Admin dashboard endpoint and staff/alerts/safety UI; Playwright smoke test (not yet committed as an automated e2e). 14 pytest tests passing.
## Session 4
- Notifications service and staff case management (13 tests passing). Case table schema changed: recreate dev DBs (no migrations yet).
## Session 3
- Research pipeline (sources, snapshots, regulatory change detection, feed leads, retries, scheduler) and fraud module. 10 tests passing.
## Session 2
- Added accounts, sessions, RBAC (11 roles), audit log; replaced single admin token.
- Added encrypted private document vault, applications (checklist, QA, status flow), data export/deletion, explainable matching.
- Tests: 6 passing (isolation, roles, upload validation, login limiting, application flow, expansion, verification).
## Session 1
- Country-agnostic config, eligibility + verification engines, discovery framework, advisor, basic UI.
