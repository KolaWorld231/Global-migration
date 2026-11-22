"""Data layer. Country-specific content lives in JSON columns / seed files, never in code."""
import os
from datetime import datetime, date
from sqlalchemy import create_engine, String, Integer, Boolean, JSON, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./pathways.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Country(Base):
    """Used for both origin and destination markets; roles are flags, not code paths."""
    __tablename__ = "countries"
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    iso3: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(100))
    is_destination: Mapped[bool] = mapped_column(Boolean, default=False)
    is_origin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    # authority, official_sites, policy_sources, regulations, fees, contacts, recruitment_rules, ...
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_verified: Mapped[date | None] = mapped_column(nullable=True)
    source_reliability: Mapped[str] = mapped_column(String(20), default="unrated")


class Pathway(Base):
    """A visa / permit / scholarship / humanitarian route with declarative requirements."""
    __tablename__ = "pathways"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))  # skilled|employer_sponsored|seasonal|study|humanitarian|family|...
    official_url: Mapped[str] = mapped_column(String(500), default="")
    requirements: Mapped[list] = mapped_column(JSON, default=list)  # see engine/eligibility.py
    documents: Mapped[list] = mapped_column(JSON, default=list)
    fees: Mapped[dict] = mapped_column(JSON, default=dict)  # {"application_usd": n|None, "notes": ""}
    excluded_nationalities: Mapped[list] = mapped_column(JSON, default=list)
    allowed_nationalities: Mapped[list] = mapped_column(JSON, default=list)  # empty = any
    last_verified: Mapped[date | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))  # employer|recruiter|government|education|ngo|migration_support
    country: Mapped[str] = mapped_column(String(2), index=True)
    origins_accepted: Mapped[list] = mapped_column(JSON, default=list)
    website: Mapped[str] = mapped_column(String(300), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # address, email, phone, fees, benefits...
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="UNVERIFIED", index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified: Mapped[date | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300))
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    pathway_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    occupation: Mapped[str] = mapped_column(String(120), default="")
    industry: Mapped[str] = mapped_column(String(120), default="")
    min_education: Mapped[str] = mapped_column(String(30), default="none")
    min_experience_years: Mapped[float] = mapped_column(default=0)
    sponsorship: Mapped[bool] = mapped_column(Boolean, default=False)
    relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    accommodation: Mapped[bool] = mapped_column(Boolean, default=False)
    applicant_fee: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = unknown
    salary_min: Mapped[float | None] = mapped_column(nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="")
    nationality_allowed: Mapped[list] = mapped_column(JSON, default=list)
    nationality_excluded: Mapped[list] = mapped_column(JSON, default=list)
    language_required: Mapped[dict] = mapped_column(JSON, default=dict)
    deadline: Mapped[date | None] = mapped_column(nullable=True)
    url: Mapped[str] = mapped_column(String(500), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    verification: Mapped[str] = mapped_column(String(30), default="UNVERIFIED", index=True)
    last_checked: Mapped[date | None] = mapped_column(nullable=True)


class Applicant(Base):
    __tablename__ = "applicants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    # field -> "private" | "advisor" | "share"; absent = private. Advisor only reads advisor/share.
    consent: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Case(Base):
    """One central case per applicant. Stage list is configurable (Setting 'case_stages')."""
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    stage: Mapped[str] = mapped_column(String(40), default="NEW_APPLICANT", index=True)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{stage, at, by}]
    updated: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class CaseNote(Base):  # internal only; never shown to applicants
    __tablename__ = "case_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    author_id: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(300))
    assignee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due: Mapped[date | None] = mapped_column(nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(10))  # inbound|outbound
    summary: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(Integer)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    email_status: Mapped[str] = mapped_column(String(20), default="NOT_REQUESTED")  # QUEUED|SENT|NOT_CONFIGURED|...
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


def init_db():
    """Apply Alembic migrations (the only supported way to create/upgrade the schema)."""
    import pathlib
    from alembic import command
    from alembic.config import Config
    root = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


# ---------------- Phase 1/5 additions: identity, audit, documents, applications ----------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant: Mapped[str] = mapped_column(String(40), default="default", index=True)  # multi-agency readiness
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    pw_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(40), default="applicant")
    applicant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    expires: Mapped[datetime] = mapped_column()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base):
    """Metadata only; bytes live encrypted in private storage and are never served by a public URL."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(Integer, index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(64))
    issued_on: Mapped[date | None] = mapped_column(nullable=True)
    expires_on: Mapped[date | None] = mapped_column(nullable=True)
    retain_until: Mapped[date | None] = mapped_column(nullable=True)
    scan_status: Mapped[str] = mapped_column(String(20), default="NOT_SCANNED")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(Integer, index=True)
    pathway_id: Mapped[str] = mapped_column(String(80))
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="PREPARING", index=True)
    deadline: Mapped[date | None] = mapped_column(nullable=True)
    submitted_on: Mapped[date | None] = mapped_column(nullable=True)
    reference: Mapped[str] = mapped_column(String(120), default="")
    follow_up_on: Mapped[date | None] = mapped_column(nullable=True)
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)  # applicant authorisation to proceed
    events: Mapped[list] = mapped_column(JSON, default=list)
    updated: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# ---------------- Research pipeline & fraud ----------------
class ResearchSource(Base):
    """Registry of permitted sources. `level` follows the source hierarchy (1 = official government ... 5 = community)."""
    __tablename__ = "research_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="page")  # page | feed
    level: Mapped[int] = mapped_column(Integer, default=1)
    policy_area: Mapped[str] = mapped_column(String(60), default="general")
    interval_hours: Mapped[int] = mapped_column(Integer, default=168)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(nullable=True)
    last_ok: Mapped[datetime | None] = mapped_column(nullable=True)


class SourceSnapshot(Base):
    """Immutable: never overwritten, giving version history for regulatory monitoring."""
    __tablename__ = "source_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    fetched: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")


class RegulatoryChange(Base):
    __tablename__ = "regulatory_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    policy_area: Mapped[str] = mapped_column(String(60))
    previous_snapshot: Mapped[int] = mapped_column(Integer)
    new_snapshot: Mapped[int] = mapped_column(Integer)
    detected: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(String(20), default="PENDING_REVIEW")
    reviewer_note: Mapped[str] = mapped_column(Text, default="")


class ResearchResult(Base):
    """A candidate lead. Never published automatically; needs review according to approval rules."""
    __tablename__ = "research_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(String(600))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    published: Mapped[str] = mapped_column(String(60), default="")
    accessed: Mapped[date] = mapped_column(default=date.today)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    source_level: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(20), default="NEEDS_REVIEW")  # NEEDS_REVIEW|APPROVED|REJECTED
    reviewer: Mapped[str] = mapped_column(String(120), default="")


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING|OK|FAILED|DEAD
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class FraudReport(Base):
    __tablename__ = "fraud_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str] = mapped_column(String(500), default="")
    indicators: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="NEW", index=True)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Workflow(Base):
    """Admin-editable automation: trigger + conditions + ordered steps (data, not code)."""
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    trigger: Mapped[str] = mapped_column(String(60), index=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING", index=True)  # RUNNING|WAITING_DELAY|WAITING_APPROVAL|DONE|FAILED|REJECTED
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    resume_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    log: Mapped[list] = mapped_column(JSON, default=list)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class AgentConfig(Base):
    """An AI staff member. Responsibilities and tool allowlist are data; authority is enforced in code (app/agents)."""
    __tablename__ = "agent_configs"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    responsibilities: Mapped[str] = mapped_column(Text, default="")
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    max_steps: Mapped[int] = mapped_column(Integer, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    model_provider: Mapped[str] = mapped_column(String(20), default="none")  # none|mock|<adapter name>
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # audit identity (cannot log in)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_key: Mapped[str] = mapped_column(String(40), index=True)
    type: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)  # QUEUED|RUNNING|NEEDS_APPROVAL|DONE|FAILED|REJECTED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    assigned_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ApprovalRequest(Base):
    """A HIGH-risk action proposed by an agent. Nothing happens until a human with the right permission approves."""
    __tablename__ = "approval_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_key: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="PENDING", index=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class OutreachDraft(Base):
    """Drafts only. The platform never sends them; a human copies/sends after review."""
    __tablename__ = "outreach_drafts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created: Mapped[datetime] = mapped_column(default=datetime.utcnow)
