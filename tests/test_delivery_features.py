import base64
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

os.environ.setdefault("PYTHONWARNINGS", "ignore")
os.environ["TIKAWANG_DATABASE_URL"] = "sqlite:///data/test_tikawang.db"
os.environ["TIKAWANG_STORAGE_DIR"] = "storage_test"

TEST_SUPER_USERNAME = os.environ["TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME"]
TEST_SUPER_PASSWORD = os.environ["TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD"]

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app, startup
from app.models import Card, Redemption, SecurityAttempt, TemporaryDownload, User
from app.security import hash_password
from app.services import reset_storage_for_tests


PUBLIC_BASE_URL = "https://cdk.ambition.qzz.io"


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_storage_for_tests()
    startup()


def login_admin(client: TestClient):
    return client.post(
        "/admin/login",
        data={"username": TEST_SUPER_USERNAME, "password": TEST_SUPER_PASSWORD},
        follow_redirects=False,
    )


def encode_jwt(payload: dict) -> str:
    def encode_part(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode_part({'alg': 'none', 'typ': 'JWT'})}.{encode_part(payload)}.sig"


def cpa_payload(email: str = "delivery@example.com") -> bytes:
    account_id = "acct_delivery"
    user_id = "user_delivery"
    access_token = encode_jwt(
        {
            "iat": 1_700_000_000,
            "exp": 1_700_086_400,
            "client_id": "client_delivery",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_user_id": user_id,
            },
        }
    )
    id_token = encode_jwt(
        {
            "sub": user_id,
            "email": email,
            "tier": "plus",
            "https://api.openai.com/auth": {
                "organizations": [{"id": "org_delivery"}],
            },
        }
    )
    return json.dumps(
        {
            "type": "codex",
            "account_id": account_id,
            "email": email,
            "access_token": access_token,
            "id_token": id_token,
            "refresh_token": "refresh_delivery",
            "last_refresh": "2026-07-14T00:00:00Z",
        }
    ).encode("utf-8")


def chatgpt_auth_payload(email: str = "raw-auth@example.com") -> tuple[bytes, dict[str, str]]:
    account_id = "acct_raw_auth"
    user_id = "user_raw_auth"
    access_token = encode_jwt(
        {
            "iat": 1_710_000_000,
            "exp": 1_710_086_400,
            "client_id": "client_raw_auth",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_user_id": user_id,
            },
        }
    )
    id_token = encode_jwt(
        {
            "sub": user_id,
            "email": email,
            "tier": "plus",
            "https://api.openai.com/auth": {
                "organizations": [{"id": "org_raw_auth"}],
            },
        }
    )
    expected = {
        "account_id": account_id,
        "email": email,
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": "refresh_raw_auth",
        "last_refresh": "2026-07-14T08:30:00Z",
    }
    raw = {
        "auth_mode": "chatgpt",
        "email": email,
        "tokens": {
            "account_id": account_id,
            "access_token": access_token,
            "id_token": id_token,
            "refresh_token": expected["refresh_token"],
        },
        "last_refresh": expected["last_refresh"],
    }
    return json.dumps(raw).encode("utf-8"), expected


def local_download_path(download_url: str) -> str:
    parsed = urlsplit(download_url)
    assert f"{parsed.scheme}://{parsed.netloc}" == PUBLIC_BASE_URL
    assert parsed.path.startswith("/d/")
    return parsed.path


def prepare_redeemable_card(client: TestClient) -> tuple[int, str]:
    assert login_admin(client).status_code == 303
    upload = client.post(
        "/admin/uploads",
        files={"file": ("delivery@example.com.json", cpa_payload(), "application/json")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    created = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        card = db.query(Card).one()
        assert card.status == "pending"
        return card.id, card.code


def redeem_as_link(client: TestClient, card_code: str) -> dict:
    response = client.post(
        "/api/redeem",
        data={"card_code": card_code, "output_format": "cpa", "delivery": "link"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["expires_in"] == 86400
    local_download_path(payload["download_url"])
    return payload


def test_direct_delivery_parameter_cannot_bypass_temporary_link() -> None:
    reset_db()
    with TestClient(app) as client:
        _, code = prepare_redeemable_card(client)
        response = client.post(
            "/api/redeem",
            data={"card_code": code, "output_format": "cpa", "delivery": "direct"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["expires_in"] == 86400
    local_download_path(payload["download_url"])


def test_cpa_to_sub_and_sub_to_cpa_conversion_downloads() -> None:
    reset_db()
    with TestClient(app) as client:
        to_sub = client.post(
            "/api/convert",
            data={"target_format": "sub"},
            files={"files": ("source@example.com.json", cpa_payload("source@example.com"), "application/json")},
        )
        assert to_sub.status_code == 200
        sub_result = to_sub.json()
        assert sub_result["source_format"] == "CPA"
        assert sub_result["target_format"] == "SUB"
        assert sub_result["account_count"] == 1
        assert sub_result["expires_in"] == 600
        with SessionLocal() as db:
            convert_link = db.query(TemporaryDownload).filter_by(purpose="convert").one()
            assert convert_link.expires_at - convert_link.created_at == timedelta(minutes=10)

        sub_download = client.get(local_download_path(sub_result["download_url"]))
        assert sub_download.status_code == 200
        sub_payload = sub_download.json()
        assert sub_payload["proxies"] == []
        assert sub_payload["accounts"][0]["credentials"]["email"] == "source@example.com"

        to_cpa = client.post(
            "/api/convert",
            data={"target_format": "cpa"},
            files={"files": ("sub2api.json", sub_download.content, "application/json")},
        )
        assert to_cpa.status_code == 200
        cpa_result = to_cpa.json()
        assert cpa_result["source_format"] == "SUB"
        assert cpa_result["target_format"] == "CPA"
        assert cpa_result["account_count"] == 1
        assert cpa_result["expires_in"] == 600

        cpa_download = client.get(local_download_path(cpa_result["download_url"]))
        assert cpa_download.status_code == 200
        converted = cpa_download.json()
        assert converted["type"] == "codex"
        assert converted["account_id"] == "acct_delivery"
        assert converted["email"] == "source@example.com"
        assert converted["refresh_token"] == "refresh_delivery"


def test_multiple_cpa_uploads_convert_to_one_json_array() -> None:
    reset_db()
    with TestClient(app) as client:
        response = client.post(
            "/api/convert",
            data={"target_format": "cpa"},
            files=[
                ("files", ("first.json", cpa_payload("first@example.com"), "application/json")),
                ("files", ("second.json", cpa_payload("second@example.com"), "application/json")),
            ],
        )
        assert response.status_code == 200
        result = response.json()
        assert result["account_count"] == 2
        assert result["expires_in"] == 600
        with SessionLocal() as db:
            file_path = Path(db.query(TemporaryDownload).filter_by(purpose="convert").one().file_path)

        download = client.get(local_download_path(result["download_url"]))
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/json"
        accounts = download.json()
        assert isinstance(accounts, list)
        assert len(accounts) == 2
        assert {account["email"] for account in accounts} == {
            "first@example.com",
            "second@example.com",
        }
        repeat = client.get(local_download_path(result["download_url"]))
        assert repeat.status_code == 410
        assert not file_path.exists()


def test_raw_chatgpt_auth_json_converts_to_flat_cpa_and_sub() -> None:
    reset_db()
    raw_auth, expected = chatgpt_auth_payload()
    with TestClient(app) as client:
        to_cpa = client.post(
            "/api/convert",
            data={"target_format": "cpa"},
            files={"files": ("raw-chatgpt-auth.json", raw_auth, "application/json")},
        )
        assert to_cpa.status_code == 200
        cpa_result = to_cpa.json()
        assert cpa_result["target_format"] == "CPA"
        assert cpa_result["account_count"] == 1

        cpa_download = client.get(local_download_path(cpa_result["download_url"]))
        assert cpa_download.status_code == 200
        cpa = cpa_download.json()
        assert cpa["type"] == "codex"
        assert cpa["account_id"] == expected["account_id"]
        assert cpa["email"] == expected["email"]
        assert cpa["access_token"] == expected["access_token"]
        assert cpa["id_token"] == expected["id_token"]
        assert cpa["refresh_token"] == expected["refresh_token"]
        assert cpa["last_refresh"] == expected["last_refresh"]
        assert "auth_mode" not in cpa
        assert "tokens" not in cpa

        to_sub = client.post(
            "/api/convert",
            data={"target_format": "sub"},
            files={"files": ("raw-chatgpt-auth.json", raw_auth, "application/json")},
        )
        assert to_sub.status_code == 200
        sub_result = to_sub.json()
        assert sub_result["target_format"] == "SUB"
        assert sub_result["account_count"] == 1

        sub_download = client.get(local_download_path(sub_result["download_url"]))
        assert sub_download.status_code == 200
        sub = sub_download.json()
        assert sub["proxies"] == []
        assert len(sub["accounts"]) == 1
        account = sub["accounts"][0]
        assert account["platform"] == "openai"
        assert account["type"] == "oauth"
        assert account["credentials"]["chatgpt_account_id"] == expected["account_id"]
        assert account["credentials"]["email"] == expected["email"]
        assert account["credentials"]["access_token"] == expected["access_token"]
        assert account["credentials"]["id_token"] == expected["id_token"]
        assert account["credentials"]["refresh_token"] == expected["refresh_token"]
        assert account["extra"]["last_refresh"] == expected["last_refresh"]


def test_redeem_link_uses_public_domain_and_is_downloadable() -> None:
    reset_db()
    with TestClient(app) as client:
        _, card_code = prepare_redeemable_card(client)
        result = redeem_as_link(client, card_code)
        with SessionLocal() as db:
            file_path = Path(db.query(TemporaryDownload).one().file_path)

        download = client.get(local_download_path(result["download_url"]))
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/json"
        assert download.json()["email"] == "delivery@example.com"
        repeat = client.get(local_download_path(result["download_url"]))
        assert repeat.status_code == 410
        assert not file_path.exists()

    with SessionLocal() as db:
        link = db.query(TemporaryDownload).one()
        assert link.purpose == "redeem"
        assert link.download_count == 1
        assert link.last_download_at is not None
        assert link.revoked_at is not None
        assert link.expires_at - link.created_at == timedelta(hours=24)


def test_lookup_restores_the_original_file_without_consuming_another_redemption() -> None:
    reset_db()
    with TestClient(app) as client:
        lookup_page = client.get("/lookup")
        assert lookup_page.status_code == 200
        assert "忘记账号？" in lookup_page.text
        assert "已兑换过的兑换码" in lookup_page.text
        assert 'data-endpoint="/api/lookup"' in lookup_page.text

        card_id, card_code = prepare_redeemable_card(client)
        redeem_as_link(client, card_code)

        lookup = client.post("/api/lookup", data={"card_code": card_code})
        assert lookup.status_code == 200
        payload = lookup.json()
        assert payload["ok"] is True
        assert payload["expires_in"] == 86400

        restored = client.get(local_download_path(payload["download_url"]))
        assert restored.status_code == 200
        assert restored.json()["email"] == "delivery@example.com"

    with SessionLocal() as db:
        card = db.get(Card, card_id)
        assert card.redemption_count == 1
        assert db.query(Redemption).filter_by(card_id=card_id).count() == 1
        lookup_link = db.query(TemporaryDownload).filter_by(purpose="lookup").one()
        assert lookup_link.expires_at - lookup_link.created_at == timedelta(hours=24)


def test_expired_link_is_cleaned_and_admin_can_regenerate_it() -> None:
    reset_db()
    with TestClient(app) as client:
        _, card_code = prepare_redeemable_card(client)
        first_result = redeem_as_link(client, card_code)

        with SessionLocal() as db:
            first_link = db.query(TemporaryDownload).one()
            first_link_id = first_link.id
            first_file = Path(first_link.file_path)
            first_link.expires_at = datetime.utcnow() - timedelta(seconds=1)
            redemption_id = db.query(Redemption).one().id
            db.commit()
        assert first_file.is_file()

        expired = client.get(local_download_path(first_result["download_url"]))
        assert expired.status_code == 410
        assert "链接已失效" in expired.text

        regenerated = client.post(f"/admin/redemptions/{redemption_id}/link")
        assert regenerated.status_code == 200
        regenerated_result = regenerated.json()
        assert regenerated_result["ok"] is True
        assert regenerated_result["expires_in"] == 86400
        assert regenerated_result["download_url"] != first_result["download_url"]
        assert not first_file.exists()

        replacement = client.get(local_download_path(regenerated_result["download_url"]))
        assert replacement.status_code == 200
        assert replacement.json()["email"] == "delivery@example.com"

    with SessionLocal() as db:
        old_link = db.get(TemporaryDownload, first_link_id)
        assert old_link.revoked_at is not None
        new_link = db.query(TemporaryDownload).filter(TemporaryDownload.id != first_link_id).one()
        assert new_link.purpose == "regenerate"
        assert new_link.redemption_id == redemption_id
        assert new_link.expires_at - new_link.created_at == timedelta(hours=24)


def test_redeem_brute_force_is_rate_limited_with_429() -> None:
    reset_db()
    invalid_code = "f" * 32
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post(
                "/api/redeem",
                data={"card_code": invalid_code, "output_format": "cpa", "delivery": "link"},
            )
            assert response.status_code == 400

        blocked = client.post(
            "/api/redeem",
            data={"card_code": invalid_code, "output_format": "cpa", "delivery": "link"},
        )

    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "900"
    assert blocked.json() == {"ok": False, "error": "尝试次数过多，请稍后再试"}
    with SessionLocal() as db:
        assert db.query(SecurityAttempt).filter_by(kind="redeem", succeeded=False).count() == 3


def test_non_admin_account_cannot_log_in_to_management() -> None:
    reset_db()
    with SessionLocal() as db:
        db.add(
            User(
                username="ordinary-user",
                password_hash=hash_password("correct-password"),
                role="user",
                is_active=True,
            )
        )
        db.commit()

    with TestClient(app) as client:
        login = client.post(
            "/admin/login",
            data={"username": "ordinary-user", "password": "correct-password"},
            follow_redirects=False,
        )
        assert login.status_code == 401
        assert "账号或密码错误" in login.text

        protected = client.get("/admin", follow_redirects=False)
        assert protected.status_code == 303
        assert protected.headers["location"] == "/admin/login"
