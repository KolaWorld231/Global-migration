"""Private encrypted document storage. Files are never addressable by URL; every read is authorised and audited."""
import os, pathlib, uuid
from cryptography.fernet import Fernet

STORAGE = pathlib.Path(os.environ.get("STORAGE_DIR", "./storage"))
MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {  # extension -> (magic prefixes, content type)
    "pdf": ([b"%PDF-"], "application/pdf"),
    "png": ([b"\x89PNG\r\n\x1a\n"], "image/png"),
    "jpg": ([b"\xff\xd8\xff"], "image/jpeg"), "jpeg": ([b"\xff\xd8\xff"], "image/jpeg"),
    "docx": ([b"PK\x03\x04"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "doc": ([b"\xd0\xcf\x11\xe0"], "application/msword"),
}
DOC_TYPES = ["passport", "cv", "certificate", "transcript", "reference", "police_clearance", "medical",
             "language_test", "licence", "offer_letter", "other"]


def _fernet():
    key = os.environ.get("DOC_ENCRYPTION_KEY")
    if not key:  # dev fallback: persisted locally with 0600. Production must supply a managed key.
        STORAGE.mkdir(parents=True, exist_ok=True)
        kf = STORAGE / ".devkey"
        if not kf.exists():
            kf.write_bytes(Fernet.generate_key()); kf.chmod(0o600)
        key = kf.read_bytes().decode()
    return Fernet(key)


def validate(filename: str, data: bytes):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise ValueError(f"file type not allowed; use one of {sorted(ALLOWED)}")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("file empty or larger than 10 MB")
    if not any(data.startswith(m) for m in ALLOWED[ext][0]):
        raise ValueError("file content does not match its extension")
    return ALLOWED[ext][1]


def scan(data: bytes) -> str:
    """Malware-scan adapter. No scanner is bundled: returns NOT_SCANNED unless CLAMAV_HOST is configured
    (integration point; not yet implemented), so the UI must never imply the file was scanned."""
    return "NOT_SCANNED"


def safe_name(filename: str) -> str:
    base = os.path.basename(filename).replace("\\", "_")
    return "".join(c for c in base if c.isalnum() or c in "._- ")[:120] or "file"


def put(data: bytes) -> str:
    STORAGE.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    p = STORAGE / key
    p.write_bytes(_fernet().encrypt(data)); p.chmod(0o600)
    return key


def get(key: str) -> bytes:
    return _fernet().decrypt((STORAGE / key).read_bytes())


def remove(key: str):
    try:
        (STORAGE / key).unlink()
    except FileNotFoundError:
        pass
