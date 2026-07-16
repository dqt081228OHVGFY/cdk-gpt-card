from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import SecurityAttempt
from app.rate_limit import (
    LIMITS,
    SecurityLimitError,
    begin_security_attempt,
    enforce_security_limit,
    finish_security_attempt,
    record_security_attempt,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'strict-rate-limit.db'}")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("redeem_generate", {"window": 60, "requests": 5, "failures": 5, "subject_failures": 0}),
        ("redeem", {"window": 900, "requests": 20, "failures": 5, "subject_failures": 3}),
        ("admin_login", {"window": 900, "requests": 15, "failures": 5, "subject_failures": 5}),
    ],
)
def test_strict_limits_are_configured(kind, expected):
    assert LIMITS[kind] == expected


@pytest.mark.parametrize(("kind", "request_limit"), [("redeem", 20), ("admin_login", 15)])
def test_total_request_limit_blocks_the_next_request(db, kind, request_limit):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    client = "198.51.100.10"

    for index in range(request_limit):
        enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=index))
        record_security_attempt(
            db,
            kind,
            client,
            True,
            timestamp=timestamp + timedelta(seconds=index),
        )

    with pytest.raises(SecurityLimitError) as exc_info:
        enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=request_limit))

    assert exc_info.value.retry_after == 900


@pytest.mark.parametrize("kind", ["redeem", "admin_login"])
def test_five_client_failures_block_the_next_attempt(db, kind):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    client = "198.51.100.20"

    for index in range(5):
        enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=index))
        record_security_attempt(
            db,
            kind,
            client,
            False,
            timestamp=timestamp + timedelta(seconds=index),
        )

    with pytest.raises(SecurityLimitError):
        enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=5))


@pytest.mark.parametrize(("kind", "subject_limit"), [("redeem", 3), ("admin_login", 5)])
def test_subject_failure_limit_applies_across_clients(db, kind, subject_limit):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    subject = "shared-cdk-or-account"

    for index in range(subject_limit):
        client = f"198.51.100.{30 + index}"
        enforce_security_limit(db, kind, client, subject, timestamp + timedelta(seconds=index))
        record_security_attempt(
            db,
            kind,
            client,
            False,
            subject,
            timestamp=timestamp + timedelta(seconds=index),
        )

    with pytest.raises(SecurityLimitError):
        enforce_security_limit(
            db,
            kind,
            "203.0.113.99",
            subject,
            timestamp + timedelta(seconds=subject_limit),
        )


@pytest.mark.parametrize("kind", ["redeem", "admin_login"])
def test_failures_expire_after_the_fifteen_minute_window(db, kind):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    client = "198.51.100.50"

    for _ in range(5):
        record_security_attempt(db, kind, client, False, timestamp=timestamp)

    with pytest.raises(SecurityLimitError):
        enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=900))

    enforce_security_limit(db, kind, client, timestamp=timestamp + timedelta(seconds=900, microseconds=1))


def test_in_flight_attempts_consume_the_request_budget(db):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    client = "198.51.100.70"
    attempt_ids = [
        begin_security_attempt(db, "admin_login", client, timestamp=timestamp + timedelta(seconds=index))
        for index in range(LIMITS["admin_login"]["requests"])
    ]

    with pytest.raises(SecurityLimitError):
        begin_security_attempt(db, "admin_login", client, timestamp=timestamp + timedelta(seconds=30))

    finish_security_attempt(db, attempt_ids[0], False, "invalid_credentials")
    assert db.get(SecurityAttempt, attempt_ids[0]).succeeded is False


def test_redeem_generate_blocks_after_five_requests_per_minute(db):
    timestamp = datetime(2026, 7, 14, 4, 0, 0)
    client = "198.51.100.88"

    for index in range(5):
        begin_security_attempt(db, "redeem_generate", client, timestamp=timestamp + timedelta(seconds=index))

    with pytest.raises(SecurityLimitError) as exc_info:
        begin_security_attempt(db, "redeem_generate", client, timestamp=timestamp + timedelta(seconds=30))

    assert exc_info.value.retry_after == 60
