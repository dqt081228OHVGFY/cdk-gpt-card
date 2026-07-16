from datetime import datetime, timedelta
import hashlib
from threading import Lock

from fastapi import Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .config import trust_proxy_headers
from .models import SecurityAttempt


class SecurityLimitError(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


LIMITS = {
    "redeem_generate": {"window": 60, "requests": 5, "failures": 5, "subject_failures": 0},
    "redeem": {"window": 900, "requests": 20, "failures": 5, "subject_failures": 3},
    "lookup": {"window": 900, "requests": 20, "failures": 5, "subject_failures": 3},
    "admin_login": {"window": 900, "requests": 60, "failures": 5, "subject_failures": 5},
    "convert": {"window": 3600, "requests": 20, "failures": 10, "subject_failures": 0},
}
_ATTEMPT_LOCK = Lock()


def identifier_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def request_client_identifier(request: Request) -> str:
    if trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def _latest_completed_success(
    db: Session,
    kind: str,
    cutoff: datetime,
    *,
    client_hash: str | None = None,
    subject_hash: str | None = None,
) -> datetime | None:
    conditions = [
        SecurityAttempt.kind == kind,
        SecurityAttempt.succeeded.is_(True),
        SecurityAttempt.attempted_at >= cutoff,
        or_(SecurityAttempt.detail.is_(None), SecurityAttempt.detail != "started"),
    ]
    if client_hash is not None:
        conditions.append(SecurityAttempt.client_hash == client_hash)
    if subject_hash is not None:
        conditions.append(SecurityAttempt.subject_hash == subject_hash)
    return db.scalar(select(func.max(SecurityAttempt.attempted_at)).where(*conditions))


def enforce_security_limit(
    db: Session,
    kind: str,
    client_identifier: str,
    subject: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    settings = LIMITS[kind]
    timestamp = timestamp or datetime.utcnow()
    cutoff = timestamp - timedelta(seconds=settings["window"])
    client_hash = identifier_hash(client_identifier)
    base = [
        SecurityAttempt.kind == kind,
        SecurityAttempt.client_hash == client_hash,
        SecurityAttempt.attempted_at >= cutoff,
    ]
    request_count = db.scalar(select(func.count()).select_from(SecurityAttempt).where(*base)) or 0
    failure_base = [*base, SecurityAttempt.succeeded.is_(False)]
    if kind == "admin_login":
        latest_client_success = _latest_completed_success(db, kind, cutoff, client_hash=client_hash)
        if latest_client_success is not None:
            failure_base.append(SecurityAttempt.attempted_at > latest_client_success)
    failure_count = db.scalar(select(func.count()).select_from(SecurityAttempt).where(*failure_base)) or 0
    blocked = request_count >= settings["requests"] or failure_count >= settings["failures"]

    if subject and settings["subject_failures"]:
        subject_hash = identifier_hash(subject)
        subject_conditions = [
            SecurityAttempt.kind == kind,
            SecurityAttempt.subject_hash == subject_hash,
            SecurityAttempt.succeeded.is_(False),
            SecurityAttempt.attempted_at >= cutoff,
        ]
        if kind == "admin_login":
            latest_subject_success = _latest_completed_success(db, kind, cutoff, subject_hash=subject_hash)
            if latest_subject_success is not None:
                subject_conditions.append(SecurityAttempt.attempted_at > latest_subject_success)
        subject_count = db.scalar(
            select(func.count()).select_from(SecurityAttempt).where(*subject_conditions)
        ) or 0
        blocked = blocked or subject_count >= settings["subject_failures"]

    if blocked:
        raise SecurityLimitError("尝试次数过多，请稍后再试", settings["window"])


def begin_security_attempt(
    db: Session,
    kind: str,
    client_identifier: str,
    subject: str | None = None,
    timestamp: datetime | None = None,
) -> int:
    timestamp = timestamp or datetime.utcnow()
    with _ATTEMPT_LOCK:
        enforce_security_limit(db, kind, client_identifier, subject, timestamp)
        attempt = SecurityAttempt(
            kind=kind,
            client_hash=identifier_hash(client_identifier),
            subject_hash=identifier_hash(subject) if subject else None,
            succeeded=True,
            detail="started",
            attempted_at=timestamp,
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
        return attempt.id


def finish_security_attempt(
    db: Session,
    attempt_id: int,
    succeeded: bool,
    detail: str | None = None,
) -> None:
    attempt = db.get(SecurityAttempt, attempt_id)
    if not attempt:
        return
    attempt.succeeded = succeeded
    attempt.detail = (detail or "")[:255] or None
    db.commit()


def record_security_attempt(
    db: Session,
    kind: str,
    client_identifier: str,
    succeeded: bool,
    subject: str | None = None,
    detail: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    db.add(
        SecurityAttempt(
            kind=kind,
            client_hash=identifier_hash(client_identifier),
            subject_hash=identifier_hash(subject) if subject else None,
            succeeded=succeeded,
            detail=(detail or "")[:255] or None,
            attempted_at=timestamp or datetime.utcnow(),
        )
    )
    db.commit()


def cleanup_security_attempts(db: Session, timestamp: datetime | None = None) -> int:
    cutoff = (timestamp or datetime.utcnow()) - timedelta(days=1)
    result = db.execute(delete(SecurityAttempt).where(SecurityAttempt.attempted_at < cutoff))
    db.commit()
    return result.rowcount or 0
