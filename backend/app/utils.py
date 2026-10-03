import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import ActivityEvent


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc)


def log_event(db: Session, message: str, *, actor: str = "system", kind: str = "info",
              job_id: int | None = None, application_id: int | None = None, data: dict | None = None):
    db.add(ActivityEvent(job_id=job_id, application_id=application_id, actor=actor, kind=kind,
                         message=message, data=data))


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
_URL = re.compile(r"https?://\S+|www\.\S+|linkedin\.com/\S+|github\.com/\S+")


def anonymize(text: str, name: str = "") -> str:
    """Strip identity details before an LLM scores a resume (reduces bias)."""
    out = _EMAIL.sub("[email]", text)
    out = _URL.sub("[link]", out)
    out = _PHONE.sub("[phone]", out)
    for part in (name or "").split():
        if len(part) > 2:
            out = re.sub(rf"\b{re.escape(part)}\b", "[candidate]", out, flags=re.IGNORECASE)
    return out


def bullet(items) -> str:
    return "\n".join(f"- {i}" for i in (items or []))
