"""Authentication, RBAC, audit. Pure stdlib crypto for passwords (scrypt)."""
import hashlib, hmac, os, secrets, time
from datetime import datetime, timedelta
from fastapi import Header, HTTPException, Depends
from .db import SessionLocal, User, AuthSession, AuditLog

SESSION_HOURS = 12
MAX_FAILS, WINDOW = 5, 900
_fails: dict[str, list[float]] = {}

# Least privilege. Note: super_admin deliberately has no document.read; documents need
# a document-capable role AND the applicant's consent (see can_read_documents).
PERMS = {
    "super_admin": {"catalog.manage", "org.manage", "org.verify", "opp.manage", "research.run", "user.manage",
                    "audit.read", "applicant.read", "case.manage"},
    "agency_director": {"audit.read", "applicant.read", "case.manage", "org.manage", "opp.manage"},
    "research_analyst": {"catalog.manage", "org.manage", "opp.manage", "research.run"},
    "compliance_reviewer": {"org.verify", "catalog.verify", "audit.read"},
    "applicant_advisor": {"applicant.read", "case.manage"},
    "recruitment_specialist": {"org.manage", "opp.manage"},
    "document_reviewer": {"applicant.read", "document.read"},
    "finance_officer": set(),
    "support_officer": {"applicant.read"},
    "auditor": {"audit.read"},
    "applicant": set(),
}
PERMS["super_admin"] |= {"case.configure", "notify.run", "dashboard.view"}
PERMS["agency_director"] |= {"dashboard.view"}
PERMS["auditor"] |= {"dashboard.view"}
PERMS["compliance_reviewer"] |= {"dashboard.view"}
PERMS["agency_director"] |= {"case.configure"}
PERMS["super_admin"] |= {"catalog.verify", "fraud.investigate"}
PERMS["compliance_reviewer"] |= {"fraud.investigate", "research.review"}
PERMS["super_admin"] |= {"research.review"}
PERMS["research_analyst"] |= {"research.review"}
ROLES = set(PERMS)


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        c = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(c.hex(), h)
    except Exception:
        return False


def valid_password(pw: str):
    if len(pw) < 10 or pw.lower() == pw or not any(c.isdigit() for c in pw):
        raise HTTPException(422, "password must be 10+ chars with upper-case letters and digits")


def audit(s, user_id, action, target="", **detail):
    s.add(AuditLog(user_id=user_id, action=action, target=str(target), detail=detail))
    s.commit()


def rate_limited(key):
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < WINDOW]
    return len(_fails[key]) >= MAX_FAILS


def note_fail(key):
    _fails.setdefault(key, []).append(time.time())


def login(s, email, pw):
    email = email.strip().lower()
    if rate_limited(email):
        raise HTTPException(429, "too many attempts; try later")
    u = s.query(User).filter_by(email=email).first()
    if not u or not u.active or not check_pw(pw, u.pw_hash):
        note_fail(email)
        audit(s, None, "login_failed", email)
        raise HTTPException(401, "invalid credentials")
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=u.id,
                      expires=datetime.utcnow() + timedelta(hours=SESSION_HOURS)))
    s.commit()
    audit(s, u.id, "login")
    return token


def current_user(authorization: str = Header(default="")) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "login required")
    th = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with SessionLocal() as s:
        sess = s.get(AuthSession, th)
        if not sess or sess.expires < datetime.utcnow():
            raise HTTPException(401, "session expired")
        u = s.get(User, sess.user_id)
        if not u or not u.active:
            raise HTTPException(401, "account disabled")
        s.expunge(u)
        return u


def require(perm: str):
    def dep(u: User = Depends(current_user)) -> User:
        if perm not in PERMS.get(u.role, set()):
            with SessionLocal() as s:
                audit(s, u.id, "permission_denied", perm)
            raise HTTPException(403, f"missing permission: {perm}")
        return u
    return dep


def can_access_applicant(u: User, aid: int, staff_perm="applicant.read") -> bool:
    return u.applicant_id == aid or staff_perm in PERMS.get(u.role, set())


def revoke(s, token):
    th = hashlib.sha256(token.encode()).hexdigest()
    x = s.get(AuthSession, th)
    if x:
        s.delete(x); s.commit()
