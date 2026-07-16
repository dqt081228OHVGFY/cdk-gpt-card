from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import services
from app.database import Base
from app.models import Card, ManagedFile, Redemption, User


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def creator(db: Session) -> User:
    user = User(
        username="card-policy-admin",
        password_hash="not-a-real-password-hash",
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def add_inventory(db: Session, creator: User, directory: Path, count: int) -> list[ManagedFile]:
    files = []
    for index in range(count):
        source = directory / f"account-{index}.json"
        source.write_text(f'{{"account": {index}}}', encoding="utf-8")
        item = ManagedFile(
            original_name=source.name,
            stored_path=str(source),
            generated_at=datetime.utcnow(),
            uploader_id=creator.id,
            status="available",
            source_format="cpa",
        )
        db.add(item)
        files.append(item)
    db.commit()
    return files


def test_multi_use_card_reuses_first_inventory_until_limit(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    inventory = add_inventory(db, creator, tmp_path, 4)
    card = services.create_cards(db, creator, file_count=1)[0]
    card.max_redemptions = 3
    db.commit()

    assert card.status == "pending"
    expected_statuses = ["pending", "pending", "sold"]
    for expected_count, expected_status in enumerate(expected_statuses, start=1):
        if expected_count == 2:
            monkeypatch.setattr(
                services,
                "build_sub2api_config",
                lambda files: {"accounts": [{"file_id": files[0].id}], "proxies": []},
            )
            output_path, _download_name = services.redeem_cards_as_sub2api(db, card.code)
            assert output_path.is_file()
        else:
            assert services.redeem_card(db, card.code).is_file()
        current = db.get(Card, card.id)
        assert current.redemption_count == expected_count
        assert current.status == expected_status

    redemptions = db.query(Redemption).filter_by(card_id=card.id).order_by(Redemption.id).all()
    redeemed_file_ids = [int(item.file_ids) for item in redemptions]
    assert len(redemptions) == 3
    assert [item.output_format for item in redemptions] == ["cpa", "sub", "cpa"]
    assert len(set(redeemed_file_ids)) == 1
    assert redeemed_file_ids[0] in {item.id for item in inventory}
    assert db.get(ManagedFile, redeemed_file_ids[0]).status == "sold"
    assert db.query(ManagedFile).filter_by(status="available").count() == 3

    with pytest.raises(services.ServiceError, match="已使用"):
        services.redeem_card(db, card.code)
    assert db.get(Card, card.id).redemption_count == 3


def test_multiple_cards_redeem_as_cpa_zip_package(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    add_inventory(db, creator, tmp_path, 2)
    cards = services.create_cards(db, creator, file_count=1, quantity=2)
    db.commit()

    output_path = services.redeem_cards(db, "\n".join(card.code for card in cards))

    assert output_path.suffix == ".zip"
    with zipfile.ZipFile(BytesIO(output_path.read_bytes())) as archive:
        payloads = [json.loads(archive.read(name)) for name in archive.namelist()]
    assert sorted(item["account"] for item in payloads) == [0, 1]
    redemptions = db.query(Redemption).order_by(Redemption.card_id).all()
    assert len(redemptions) == 2
    assert all(item.download_path == str(output_path) for item in redemptions)


def test_multiple_cpa_conversions_return_one_json_array(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    uploads = [
        (
            f"account-{index}.json",
            json.dumps(
                {
                    "type": "codex",
                    "account_id": f"acct-{index}",
                    "email": f"account-{index}@example.com",
                    "access_token": "not-a-jwt",
                }
            ).encode("utf-8"),
        )
        for index in range(2)
    ]

    output_path, download_name, media_type, source_label, account_count = services.convert_json_uploads(
        uploads,
        "cpa",
        datetime(2026, 7, 14, 12, 0, 0),
    )

    assert output_path.suffix == ".json"
    assert download_name == "cpa-20260714120000-2-accounts.json"
    assert media_type == "application/json"
    assert source_label == "CPA"
    assert account_count == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert [item["email"] for item in payload] == ["account-0@example.com", "account-1@example.com"]


def test_card_expiration_is_strict_and_does_not_consume_inventory(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    timestamp = datetime(2026, 7, 14, 12, 0, 0)
    monkeypatch.setattr(services, "now_utc", lambda: timestamp)
    inventory = add_inventory(db, creator, tmp_path, 1)[0]
    card = services.create_cards(db, creator, file_count=1)[0]
    card.status = "pending"
    card.expires_at = timestamp
    db.commit()

    with pytest.raises(services.ServiceError, match="已过期"):
        services.redeem_card(db, card.code)

    assert db.get(Card, card.id).redemption_count == 0
    assert db.get(Card, card.id).status == "pending"
    assert db.get(ManagedFile, inventory.id).status == "available"
    assert db.query(Redemption).count() == 0


def test_failed_output_rolls_back_usage_counter(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    inventory = add_inventory(db, creator, tmp_path, 1)[0]
    card = services.create_cards(db, creator, file_count=1)[0]
    card.max_redemptions = 2
    db.commit()

    monkeypatch.setattr(services.shutil, "copyfile", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError, match="boom"):
        services.redeem_card(db, card.code)

    current = db.get(Card, card.id)
    assert current.redemption_count == 0
    assert current.status == "pending"
    assert current.used_at is None
    assert db.get(ManagedFile, inventory.id).status == "available"


def test_failed_repeat_output_rolls_back_without_rebinding_inventory(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    inventory = add_inventory(db, creator, tmp_path, 2)
    card = services.create_cards(db, creator, file_count=1)[0]
    card.max_redemptions = 3
    db.commit()

    assert services.redeem_card(db, card.code).is_file()
    original = db.query(Redemption).filter_by(card_id=card.id).one()
    bound_file_id = int(original.file_ids)
    remaining_file_id = next(item.id for item in inventory if item.id != bound_file_id)

    monkeypatch.setattr(services.shutil, "copyfile", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError, match="boom"):
        services.redeem_card(db, card.code)

    current = db.get(Card, card.id)
    assert current.redemption_count == 1
    assert current.status == "pending"
    assert db.query(Redemption).filter_by(card_id=card.id).count() == 1
    assert db.get(ManagedFile, bound_file_id).status == "sold"
    assert db.get(ManagedFile, remaining_file_id).status == "available"


def test_missing_original_redemption_never_rebinds_inventory(
    db: Session,
    creator: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(services, "DOWNLOAD_DIR", download_dir)
    inventory = add_inventory(db, creator, tmp_path, 1)[0]
    card = services.create_cards(db, creator, file_count=1)[0]
    card.max_redemptions = 3
    card.redemption_count = 1
    db.commit()

    with pytest.raises(services.ServiceError, match="首次兑换记录不存在"):
        services.redeem_card(db, card.code)

    current = db.get(Card, card.id)
    assert current.redemption_count == 1
    assert current.status == "pending"
    assert db.get(ManagedFile, inventory.id).status == "available"
    assert db.query(Redemption).count() == 0


def test_concurrent_reservations_never_exceed_usage_limit(tmp_path: Path) -> None:
    database_path = tmp_path / "policy.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    # Serialize transaction starts in SQLite so every worker observes the last
    # committed counter, matching row-lock behavior on server databases.
    @event.listens_for(engine, "begin")
    def begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        creator = User(
            username="concurrent-card-policy-admin",
            password_hash="not-a-real-password-hash",
            role="admin",
            is_active=True,
        )
        db.add(creator)
        db.flush()
        card = Card(
            code="a" * 32,
            creator_id=creator.id,
            file_count=1,
            max_redemptions=3,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.add(card)
        db.commit()
        card_id = card.id

    def reserve_once() -> bool:
        with Session(engine) as db:
            card = db.get(Card, card_id)
            try:
                services.reserve_cards_for_redemption(db, [card], datetime.utcnow())
                db.commit()
                return True
            except services.ServiceError:
                return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: reserve_once(), range(8)))

    with Session(engine) as db:
        card = db.get(Card, card_id)
        assert outcomes.count(True) == 3
        assert card.redemption_count == 3
        assert card.status == "sold"
    engine.dispose()
