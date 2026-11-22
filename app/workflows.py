"""Workflow engine. Step types: notify, assign_case, create_task, set_stage, delay, approval, escalate.
Everything is data in the `workflows` table; adding or changing a workflow needs no code change.
Events are emitted by the app with emit(); runs advance until they finish or must wait (delay/approval)."""
import re
from datetime import datetime, timedelta
from .db import Workflow, WorkflowRun, User, Case, Task, CaseNote, Applicant
from . import notify as notifier
from . import security
from .engine.eligibility import get_path

TRIGGERS = {"applicant.registered", "application.status_changed", "regulatory.confirmed", "fraud.reported"}
STEP_TYPES = {"notify", "assign_case", "create_task", "set_stage", "delay", "approval", "escalate"}
OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "in": lambda a, b: a in b,
       "gte": lambda a, b: a >= b, "lte": lambda a, b: a <= b}


class StepError(Exception):
    pass


def validate(trigger, conditions, steps):
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {sorted(TRIGGERS)}")
    for c in conditions:
        if not {"field", "op", "value"} <= set(c) or c["op"] not in OPS:
            raise ValueError("condition needs field, op (eq|ne|in|gte|lte), value")
    if not steps:
        raise ValueError("at least one step required")
    for st in steps:
        t = st.get("type")
        if t not in STEP_TYPES:
            raise ValueError(f"step type must be one of {sorted(STEP_TYPES)}")
        need = {"notify": {"title"}, "assign_case": set(), "create_task": {"title"}, "set_stage": {"stage"},
                "delay": {"hours"}, "approval": set(), "escalate": {"staff_id", "message"}}[t]
        if need - set(st):
            raise ValueError(f"{t} step requires {sorted(need)}")


def render(tpl, ctx):
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), "")), tpl or "")


def matches(conds, ctx):
    for c in conds:
        v = get_path(ctx, c["field"])
        if v is None or not OPS[c["op"]](v, c["value"]):
            return False
    return True


def emit(s, event, ctx):
    started = []
    for wf in s.query(Workflow).filter_by(trigger=event, active=True):
        if matches(wf.conditions, ctx):
            run = WorkflowRun(workflow_id=wf.id, context=ctx, log=[]); s.add(run); s.commit()
            advance(s, run.id); started.append(run.id)
    return started


def _case(s, ctx):
    c = s.query(Case).filter_by(applicant_id=ctx.get("applicant_id")).first()
    if not c:
        raise StepError("no case for this context")
    return c


def _user_for(s, ctx):
    if ctx.get("user_id"):
        return ctx["user_id"]
    u = s.query(User).filter_by(applicant_id=ctx.get("applicant_id")).first()
    return u.id if u else None


def _step(s, run, st):
    t, ctx = st["type"], run.context
    if t == "notify":
        uid = _user_for(s, ctx)
        if not uid:
            raise StepError("no recipient")
        notifier.notify(s, uid, st.get("kind", "account"), render(st["title"], ctx), render(st.get("body", ""), ctx),
                        key=f"wf{run.id}:{run.step_index}")
    elif t == "assign_case":
        c = _case(s, ctx)
        if st.get("staff_id"):
            target = s.get(User, st["staff_id"])
        else:  # least-loaded eligible staff
            staff = [u for u in s.query(User).filter_by(active=True)
                     if "case.manage" in security.PERMS.get(u.role, set()) and u.role != "super_admin"]
            load = lambda u: s.query(Case).filter_by(assigned_to=u.id).count()
            target = min(staff, key=load) if staff else None
        if not target or "case.manage" not in security.PERMS.get(target.role, set()):
            raise StepError("no eligible assignee")
        c.assigned_to = target.id; c.updated = datetime.utcnow()
    elif t == "create_task":
        c = _case(s, ctx)
        s.add(Task(case_id=c.id, title=render(st["title"], ctx), assignee_id=c.assigned_to,
                   due=(datetime.utcnow() + timedelta(days=int(st.get("due_in_days", 3)))).date()))
    elif t == "set_stage":
        c = _case(s, ctx)
        c.history = [*c.history, {"stage": st["stage"], "at": datetime.utcnow().isoformat(timespec="seconds"), "by": None}]
        c.stage = st["stage"]
    elif t == "escalate":
        c = _case(s, ctx)
        s.add(Task(case_id=c.id, title="ESCALATION: " + render(st["message"], ctx), assignee_id=st["staff_id"],
                   due=datetime.utcnow().date()))
        s.add(CaseNote(case_id=c.id, author_id=st["staff_id"], body=f"Escalated by workflow run {run.id}"))


def advance(s, run_id):
    run = s.get(WorkflowRun, run_id); wf = s.get(Workflow, run.workflow_id)
    run.status = "RUNNING"
    while run.step_index < len(wf.steps):
        st = wf.steps[run.step_index]
        stamp = datetime.utcnow().isoformat(timespec="seconds")
        try:
            if st["type"] == "delay":
                run.resume_at = datetime.utcnow() + timedelta(hours=float(st["hours"]))
                run.status = "WAITING_DELAY"; run.step_index += 1
                run.log = [*run.log, {"at": stamp, "step": "delay", "result": f"until {run.resume_at.isoformat(timespec='seconds')}"}]
                s.commit(); return run
            if st["type"] == "approval":
                run.status = "WAITING_APPROVAL"
                run.log = [*run.log, {"at": stamp, "step": "approval", "result": "waiting"}]
                s.commit(); return run
            _step(s, run, st)
            run.log = [*run.log, {"at": stamp, "step": st["type"], "result": "ok"}]
            run.step_index += 1
        except Exception as e:  # failure is recorded, never swallowed
            s.rollback(); run = s.get(WorkflowRun, run_id)
            run.status, run.error = "FAILED", f"step {run.step_index} ({st['type']}): {e}"[:500]
            run.log = [*run.log, {"at": stamp, "step": st["type"], "result": "FAILED", "error": str(e)[:300]}]
            s.commit(); return run
        s.commit()
    run.status = "DONE"; s.commit()
    return run


def decide(s, run_id, approve, user):
    run = s.get(WorkflowRun, run_id)
    if not run or run.status != "WAITING_APPROVAL":
        raise ValueError("run is not waiting for approval")
    wf = s.get(Workflow, run.workflow_id); st = wf.steps[run.step_index]
    perm = st.get("perm", "case.manage")
    if perm not in security.PERMS.get(user.role, set()):
        raise PermissionError(perm)
    run.log = [*run.log, {"at": datetime.utcnow().isoformat(timespec="seconds"), "step": "approval",
                          "result": "approved" if approve else "rejected", "by": user.email}]
    if not approve:
        run.status = "REJECTED"; s.commit(); return run
    run.step_index += 1; s.commit()
    return advance(s, run_id)


def tick(s, now=None):
    """Resume delayed runs whose time has come (called by the scheduler loop)."""
    now = now or datetime.utcnow()
    ids = [r.id for r in s.query(WorkflowRun).filter_by(status="WAITING_DELAY") if r.resume_at <= now]
    for i in ids:
        advance(s, i)
    return len(ids)


def retry(s, run_id):
    run = s.get(WorkflowRun, run_id)
    if not run or run.status != "FAILED":
        raise ValueError("only FAILED runs can be retried")
    run.error = ""; s.commit()
    return advance(s, run_id)


DEFAULTS = [{"name": "New applicant onboarding", "trigger": "applicant.registered", "conditions": [],
             "steps": [{"type": "notify", "kind": "account", "title": "Welcome to Global Pathways",
                        "body": "Complete your profile so we can assess pathways. We never guarantee visas or outcomes."},
                       {"type": "assign_case"}]}]


def seed_defaults(s):
    if s.query(Workflow).count():
        return
    for d in DEFAULTS:
        s.add(Workflow(**d))
    s.commit()
