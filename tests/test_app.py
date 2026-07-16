import io
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

os.environ.setdefault("PYTHONWARNINGS", "ignore")
os.environ["TIKAWANG_DATABASE_URL"] = "sqlite:///data/test_tikawang.db"
os.environ["TIKAWANG_STORAGE_DIR"] = "storage_test"

TEST_SUPER_USERNAME = os.environ["TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME"]
TEST_SUPER_PASSWORD = os.environ["TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD"]

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
import app.main as main_module
from app.main import app, card_status_label, format_dt, format_full_dt, startup, status_label
from app.services import reset_storage_for_tests


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_storage_for_tests()
    startup()


def login(client: TestClient):
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


def cpa_payload(email: str, account_id: str = "acct_1", user_id: str = "user_1") -> bytes:
    access_token = encode_jwt(
        {
            "iat": 1_700_000_000,
            "exp": 1_700_086_400,
            "client_id": "client_test",
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
                "organizations": [{"id": "org_test"}],
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
            "refresh_token": f"refresh_{user_id}",
            "last_refresh": "2026-05-23T00:00:00Z",
        }
    ).encode("utf-8")


def test_admin_login_and_inventory():
    reset_db()
    client = TestClient(app)
    response = login(client)
    assert response.status_code == 303
    inventory = client.get("/api/inventory")
    assert inventory.status_code == 200
    assert inventory.json() == {"inventory": 0}


def test_login_error_keeps_entered_credentials():
    reset_db()
    with TestClient(app) as client:
        response = client.post(
            "/admin/login",
            data={"username": TEST_SUPER_USERNAME, "password": "bad-password"},
            follow_redirects=False,
        )
    assert response.status_code == 401
    assert '<div class="login-error" role="alert">账号或密码错误</div>' in response.text
    assert f'name="username" value="{TEST_SUPER_USERNAME}"' in response.text
    assert 'name="password" type="password" value="bad-password"' in response.text


def test_admin_logout_button_stays_above_page_heading_and_clears_session():
    reset_db()
    client = TestClient(app)
    login(client)

    page = client.get("/admin")
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")

    assert '<form method="post" action="/admin/logout">' in page.text
    assert '<button class="logout-button" type="submit">退出管理</button>' in page.text
    topbar_styles = re.search(r"\.admin-topbar\s*\{(?P<body>[^}]+)\}", styles_css).group("body")
    assert "position: relative" in topbar_styles
    assert "z-index: 2" in topbar_styles

    response = client.post("/admin/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"

    protected = client.get("/admin", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"] == "/admin/login"


def test_datetime_filters_use_full_second_format():
    from datetime import datetime

    value = datetime(2026, 7, 3, 23, 4, 5)
    assert format_dt(value) == "2026-07-03 23:04:05"
    assert format_full_dt(value) == "2026-07-04 07:04:05"


def test_card_management_page_uses_directly_redeemable_statuses():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 4},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        cards[1].status = "sold"
        cards[2].status = "voided"
        db.commit()

    page = client.get("/admin/cards")
    assert page.status_code == 200
    assert "待上架" not in page.text
    assert "已上架" not in page.text
    assert "已提取" not in page.text
    assert "批量上架" not in page.text
    assert "listed" not in page.text
    assert "可用" not in page.text
    assert "可使用" in page.text
    assert "已使用" in page.text
    assert page.text.index("可使用") < page.text.index("已使用") < page.text.index("已作废")
    assert 'data-card-download' in page.text
    assert "批量作废" not in page.text
    assert 'formaction="/admin/cards/void"' not in page.text
    assert 'data-selected-count' in page.text
    assert 'class="bulk-status-select" name="target_status" disabled' in page.text
    assert 'data-copy-pending' not in page.text
    assert "复制并修改为待使用" not in page.text
    assert "<th>创建用户</th>" not in page.text
    assert "可绑定文件个数" not in page.text
    assert "账号数" in page.text
    assert '<th class="center">序号</th>' in page.text
    assert '<td class="center">1</td>' in page.text
    assert '<th class="center time-col">创建时间</th>' in page.text
    assert '<th class="center">操作用户</th>' in page.text
    assert page.text.index("创建时间") < page.text.index("操作用户")
    assert 'class="cards-code-col"' in page.text
    admin_js = Path("static/admin.js").read_text(encoding="utf-8")
    assert "data-copy-pending" not in admin_js
    assert "data-selected-count" in admin_js
    assert "select.disabled = selected.length === 0" in admin_js
    assert 'button.disabled = select.disabled' in admin_js
    assert 'currentStatus !== "available"' not in admin_js
    assert "white-control-20260714-4" in Path("templates/admin_base.html").read_text(encoding="utf-8")


def test_card_management_filters_by_first_usage_date_and_uses_narrow_status_select():
    from datetime import datetime

    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 3},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        cards[0].used_at = datetime(2026, 5, 1, 10, 0, 0)
        cards[1].used_at = datetime(2026, 5, 10, 10, 0, 0)
        cards[2].used_at = datetime(2026, 5, 20, 10, 0, 0)
        db.commit()
        expected_code = cards[1].code
        early_code = cards[0].code
        late_code = cards[2].code

    page = client.get("/admin/cards?start=2026-05-10&end=2026-05-10")
    assert page.status_code == 200
    assert expected_code in page.text
    assert early_code not in page.text
    assert late_code not in page.text
    assert 'class="filters cards-filters"' in page.text
    assert 'class="card-search-filter" name="q"' in page.text
    assert 'class="status-filter" name="status"' in page.text
    assert 'class="secondary-action filter-action" type="submit"' in page.text
    assert 'class="ghost-action filter-reset" href="/admin/cards"' in page.text
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")
    assert "grid-template-columns: 520px 130px 140px 140px 108px 96px" in styles_css
    assert ".cards-filters .card-search-filter" in styles_css
    assert ".cards-list-form table" in styles_css
    assert "table-layout: fixed" in styles_css
    assert ".cards-code-col" in styles_css


    reset_db()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "<em>sub2api JSON</em>" not in response.text
    assert response.text.count('name="output_format" value="cpa" checked') == 1
    assert "下载地址仅保留一小时" in response.text
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    assert "function initializeFormatOptions" in app_js


def test_public_redeem_uses_local_loading_without_route_animation():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")
    assert "window.fetch = fetchWithGlobalLoading" not in app_js
    assert "classList.add(\"is-route-loading\")" not in app_js
    assert 'classList.toggle("is-loading", loading)' in app_js
    assert "function setLoading" in app_js
    assert ".primary-action.is-loading::before" in styles_css
    assert "@keyframes button-spin" in styles_css
    assert ".redeem-form .primary-action" in styles_css
    assert "font-size: 18px" in styles_css


def test_login_button_matches_input_height_with_larger_text():
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")

    login_button_styles = re.search(r"\.login-card \.primary-action\.full\s*\{(?P<body>[^}]+)\}", styles_css).group("body")
    assert "height: 48px" in login_button_styles
    assert "font-size: 16px" in login_button_styles
    assert "background: var(--green)" in login_button_styles


def test_public_inventory_card_matches_small_stock_range():
    index_html = Path("templates/index.html").read_text(encoding="utf-8")
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")

    assert '<strong id="inventoryCount">0</strong>' in index_html
    assert 'fetch("/api/inventory"' in app_js
    inventory_styles = re.search(r"\.inventory-card\s*\{(?P<body>[^}]+)\}", styles_css).group("body")
    inventory_count_styles = re.search(r"\.inventory-card strong\s*\{(?P<body>[^}]+)\}", styles_css).group("body")
    assert "align-items: end" in inventory_styles
    assert 'font-family: "Aptos Display"' in inventory_count_styles
    assert "当前可用库存" in index_html
    assert "AVAILABLE ACCOUNTS" in index_html


def test_generated_card_codes_are_32_random_hex_characters():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card

        card = db.query(Card).first()
        code = card.code
        assert card.status == "pending"
    assert re.fullmatch(r"[0-9a-f]{32}", code)


def test_card_file_count_is_fixed_to_one_account_per_code():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 15, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import Card

        assert db.query(Card).count() == 0


def test_cards_page_uses_first_usage_label_and_has_detail_action():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/admin/cards")
    assert page.status_code == 200
    assert "首次使用时间" in page.text
    assert "使用详情" in page.text


def test_upload_create_card_and_redeem_json():
    reset_db()
    client = TestClient(app)
    login(client)
    for idx in range(1):
        filename = f"user{idx}@example.com___070323.json"
        response = client.post(
            "/admin/uploads",
            files={"file": (filename, cpa_payload(f"user{idx}@example.com"), "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        code = db.query(Card).first().code

    response = client.post("/api/redeem", data={"card_code": code, "output_format": "cpa"})
    assert response.status_code == 200
    assert response.json()["expires_in"] == 86400
    download = client.get(urlsplit(response.json()["download_url"]).path)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/json"
    assert download.json()["email"] == "user0@example.com"

    with SessionLocal() as db:
        from app.models import ManagedFile

        sold_files = db.query(ManagedFile).filter_by(status="sold").all()
        assert sold_files
        assert {item.sold_card.code for item in sold_files} == {code}

    assert client.get("/api/inventory").json() == {"inventory": 0}


def test_generated_card_is_immediately_redeemable():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("pending-only.json", b'{"ok": true}', "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        card = db.query(Card).first()
        code = card.code
        assert card.status == "pending"

    response = client.post("/api/redeem", data={"card_code": code, "output_format": "cpa"})
    assert response.status_code == 200
    with SessionLocal() as db:
        from app.models import Card

        assert db.query(Card).one().status == "sold"


def test_redeem_sub_returns_temporary_sub2api_json_link():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("sub@example.com.json", cpa_payload("sub@example.com"), "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        code = db.query(Card).first().code

    response = client.post("/api/redeem", data={"card_code": code, "output_format": "sub"})
    assert response.status_code == 200
    assert response.json()["expires_in"] == 86400
    download = client.get(urlsplit(response.json()["download_url"]).path)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/json"
    assert 'filename="sub2api-' in download.headers["content-disposition"]
    assert download.headers["content-disposition"].endswith('-plus.json"')
    data = download.json()
    assert data["proxies"] == []
    assert len(data["accounts"]) == 1
    account = data["accounts"][0]
    assert account["platform"] == "openai"
    assert account["type"] == "oauth"
    assert account["credentials"]["email"] == "sub@example.com"
    assert account["credentials"]["plan_type"] == "plus"
    assert account["credentials"]["chatgpt_account_id"] == "acct_1"

    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        assert db.query(Card).first().status == "sold"
        assert db.query(ManagedFile).first().status == "sold"


def test_extended_card_redeem_reuses_bound_files_without_binding_more():
    reset_db()
    client = TestClient(app)
    login(client)
    for idx in range(2):
        response = client.post(
            "/admin/uploads",
            files={"file": (f"reuse{idx}@example.com.json", cpa_payload(f"reuse{idx}@example.com"), "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        card = db.query(Card).first()
        card_id = card.id
        code = card.code

    first = client.post("/api/redeem", data={"card_code": code, "output_format": "cpa"})
    extended = client.post("/admin/cards/extend", data={"ids": card_id}, follow_redirects=False)
    second = client.post("/api/redeem", data={"card_code": code, "output_format": "sub"})
    assert first.status_code == 200
    assert extended.status_code == 303
    assert second.status_code == 200
    first_download = client.get(urlsplit(first.json()["download_url"]).path)
    second_download = client.get(urlsplit(second.json()["download_url"]).path)
    first_email = first_download.json()["email"]
    assert first_email in {"reuse0@example.com", "reuse1@example.com"}
    assert second_download.json()["accounts"][0]["credentials"]["email"] == first_email

    with SessionLocal() as db:
        from app.models import ManagedFile, Redemption

        redemptions = db.query(Redemption).filter_by(card_id=card_id).order_by(Redemption.id).all()
        assert [item.file_ids for item in redemptions] == [redemptions[0].file_ids, redemptions[0].file_ids]
        assert db.query(ManagedFile).filter_by(sold_card_id=card_id).count() == 1
        assert db.query(ManagedFile).filter_by(status="available").count() == 1


def test_concurrent_redeems_cannot_extract_same_available_file():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("race@example.com.json", cpa_payload("race@example.com"), "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        for card in cards:
            card.status = "pending"
        db.commit()
        codes = [card.code for card in cards]

    def redeem(code):
        with TestClient(app) as worker:
            return worker.post("/api/redeem", data={"card_code": code, "output_format": "cpa"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(redeem, codes))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 400]
    assert any(
        marker in response.text
        for response in responses
        if response.status_code == 400
        for marker in ("库存不足", "库存状态变化")
    )

    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        file_item = db.query(ManagedFile).one()
        assert file_item.status == "sold"
        assert file_item.sold_card_id in [card.id for card in db.query(Card).all()]
        assert db.query(Card).filter_by(status="sold").count() == 1


def test_public_redeem_ui_uses_floating_toast_and_sub_download_filename():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    index_html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'id="redeemToast"' in index_html
    assert 'id="redeemMessage"' not in index_html
    assert "inline-message" not in app_js
    assert "toast show" in app_js
    assert "clearTimeout" in app_js
    assert 'contentType.includes("application/json")' in app_js
    assert "response.ok" in app_js
    assert "data.download_url" in app_js
    assert "data.expires_at" in app_js
    assert 'id="deliveryResult"' in index_html
    assert 'name="delivery" value="link"' in index_html


def test_card_redeem_after_explicit_expiration_returns_expiry_message(monkeypatch):
    from datetime import datetime, timedelta

    import app.services as services

    reset_db()
    first_time = datetime(2026, 5, 23, 10, 0, 0)
    expired_time = datetime(2026, 5, 24, 10, 0, 1)
    client = TestClient(app)
    login(client)
    for idx in range(2):
        response = client.post(
            "/admin/uploads",
            files={
                "file": (
                    f"expired{idx}@example.com.json",
                    cpa_payload(f"expired{idx}@example.com", f"acct_expired_{idx}", f"user_expired_{idx}"),
                    "application/json",
                )
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1, "max_redemptions": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        card = db.query(Card).first()
        card.expires_at = first_time + timedelta(hours=24)
        db.commit()
        code = card.code

    current_time = [first_time]
    monkeypatch.setattr(services, "now_utc", lambda: current_time[0])

    first = client.post("/api/redeem", data={"card_code": code, "output_format": "cpa"})
    assert first.status_code == 200

    current_time[0] = expired_time
    second = client.post("/api/redeem", data={"card_code": code, "output_format": "sub"})
    assert second.status_code == 400
    assert "卡密已过期" in second.json()["error"]


def test_sub_download_uses_unique_storage_path_when_filename_timestamp_matches(monkeypatch):
    from datetime import datetime

    import app.services as services

    reset_db()
    monkeypatch.setattr(services, "now_utc", lambda: datetime(2026, 5, 23, 12, 34, 56))
    client = TestClient(app)
    login(client)
    for idx in range(2):
        response = client.post(
            "/admin/uploads",
            files={
                "file": (
                    f"sub{idx}@example.com.json",
                    cpa_payload(f"sub{idx}@example.com", f"acct_{idx}", f"user_{idx}"),
                    "application/json",
                )
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        for card in cards:
            card.status = "pending"
        db.commit()
        codes = [card.code for card in cards]

    responses = [client.post("/api/redeem", data={"card_code": code, "output_format": "sub"}) for code in codes]
    assert [response.status_code for response in responses] == [200, 200]
    assert all('filename="sub2api-20260523123456-plus.json"' in response.headers["content-disposition"] for response in responses)

    with SessionLocal() as db:
        from app.models import Redemption

        paths = [Path(item.download_path) for item in db.query(Redemption).order_by(Redemption.id)]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    assert all(path.exists() for path in paths)


def test_upload_accepts_multiple_json_files_in_one_request():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files=[
            ("file", ("batch0@example.com___070323.json", b'{"ok": true}', "application/json")),
            ("file", ("batch1@example.com___070323.json", b'{"ok": true}', "application/json")),
        ],
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/files?")
    assert "%E5%B7%B2%E5%AF%BC%E5%85%A5+2" in response.headers["location"]
    assert client.get("/api/inventory").json() == {"inventory": 2}


def test_admin_batch_download_includes_directly_redeemable_cards():
    reset_db()
    client = TestClient(app)
    login(client)
    for idx in range(10):
        response = client.post(
            "/admin/uploads",
            files={"file": (f"down{idx}@example.com___070323.json", b'{"ok": true}', "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        file_ids = [item.id for item in db.query(ManagedFile).order_by(ManagedFile.id).limit(2)]
        card_ids = [item.id for item in db.query(Card).order_by(Card.id).all()]

    response = client.post("/admin/files/download", data={"ids": file_ids})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert all(Path(name).suffix == ".json" for name in names)
    with SessionLocal() as db:
        from app.models import ManagedFile

        downloaded_files = db.query(ManagedFile).filter(ManagedFile.id.in_(file_ids)).all()
        assert {item.status for item in downloaded_files} == {"available"}
        assert all(item.latest_download_at for item in downloaded_files)

    response = client.post("/admin/files/download", data={"ids": file_ids[0], "mark_sold": "1"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == b'{"ok": true}'
    with SessionLocal() as db:
        from app.models import ManagedFile

        sold_file = db.get(ManagedFile, file_ids[0])
        untouched_file = db.get(ManagedFile, file_ids[1])
        assert sold_file.status == "sold"
        assert sold_file.sold_at is not None
        assert untouched_file.status == "available"

    response = client.post("/admin/cards/download", data={"ids": card_ids})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    downloaded_codes = response.text.strip().splitlines()
    assert len(downloaded_codes) == len(card_ids)
    assert all(re.fullmatch(r"[0-9a-f]{32}", code) for code in downloaded_codes)
    with SessionLocal() as db:
        from app.models import Card

        assert {item.status for item in db.query(Card).order_by(Card.id).all()} == {"pending"}


def test_admin_batch_checks_file_account_status_and_voids_unavailable(monkeypatch):
    reset_db()
    client = TestClient(app)
    login(client)
    for filename, payload in (
        ("usable@example.com.json", cpa_payload("usable@example.com")),
        ("empty@example.com.json", cpa_payload("empty@example.com")),
    ):
        response = client.post(
            "/admin/uploads",
            files={"file": (filename, payload, "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import ManagedFile, User

        admin = db.query(User).filter_by(username=TEST_SUPER_USERNAME).first()
        admin.quota_pool_base_url = "http://pool3.example"
        admin.quota_pool_management_key = "pool-secret"
        file_ids = [item.id for item in db.query(ManagedFile).order_by(ManagedFile.id).all()]
        db.commit()

    class FakeCLIProxyClient:
        uploads = []
        deletes = []
        init_args = []

        def __init__(self, base_url: str, management_key: str) -> None:
            self.init_args.append((base_url, management_key))

        async def upload_auth_file(self, filename: str, content: bytes):
            self.uploads.append((filename, content))
            return {"name": filename}

        async def codex_cards(self):
            name = self.uploads[-1][0]
            remaining = 31 if "usable" in name else 0
            return [
                SimpleNamespace(
                    name=name,
                    status="success" if remaining else "exhausted",
                    windows=[SimpleNamespace(remaining_percent=remaining)],
                )
            ]

        async def delete_auth_file(self, filename: str):
            self.deletes.append(filename)
            return {"status": "ok"}

    monkeypatch.setattr(main_module, "CLIProxyClient", FakeCLIProxyClient)

    response = client.post(
        "/admin/files/account-status",
        data={"ids": file_ids},
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["available"] == 1
    assert response.json()["unavailable"] == 1
    assert FakeCLIProxyClient.init_args[0] == ("http://pool3.example", "pool-secret")
    assert [name for name, _content in FakeCLIProxyClient.uploads] == [
        "usable@example.com.json",
        "empty@example.com.json",
    ]
    assert FakeCLIProxyClient.deletes == ["usable@example.com.json", "empty@example.com.json"]

    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        assert [(item.original_name, item.account_status, item.status) for item in files] == [
            ("usable@example.com", "available", "available"),
            ("empty@example.com", "unavailable", "voided"),
        ]
        assert files[1].voided_at is not None


def test_account_status_check_covers_all_file_statuses_but_only_voids_available_files(monkeypatch):
    reset_db()
    client = TestClient(app)
    login(client)
    for filename in (
        "available@example.com.json",
        "sold@example.com.json",
        "locked@example.com.json",
        "voided@example.com.json",
    ):
        response = client.post(
            "/admin/uploads",
            files={"file": (filename, cpa_payload(filename.removesuffix(".json")), "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import ManagedFile, User

        admin = db.query(User).filter_by(username=TEST_SUPER_USERNAME).first()
        admin.quota_pool_base_url = "http://pool3.example"
        admin.quota_pool_management_key = "pool-secret"
        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        file_ids = [item.id for item in files]
        files[1].status = "sold"
        files[2].status = "locked"
        files[3].status = "voided"
        db.commit()

    class FakeCLIProxyClient:
        uploaded = []

        def __init__(self, base_url: str, management_key: str) -> None:
            pass

        async def upload_auth_file(self, filename: str, content: bytes):
            self.uploaded.append(filename)
            return {"name": filename}

        async def codex_cards(self):
            return [SimpleNamespace(name=self.uploaded[-1], status="exhausted", windows=[SimpleNamespace(remaining_percent=0)])]

        async def delete_auth_file(self, filename: str):
            return {"status": "ok"}

    monkeypatch.setattr(main_module, "CLIProxyClient", FakeCLIProxyClient)

    response = client.post(
        "/admin/files/account-status",
        data={"ids": file_ids},
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
    )

    assert response.status_code == 200
    assert response.json()["unavailable"] == 4
    assert FakeCLIProxyClient.uploaded == [
        "available@example.com.json",
        "sold@example.com.json",
        "locked@example.com.json",
        "voided@example.com.json",
    ]
    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        assert [item.status for item in files] == ["voided", "sold", "locked", "voided"]
        assert [item.account_status for item in files] == ["unavailable"] * 4
        assert files[1].voided_at is None
        assert files[2].voided_at is None


def test_files_page_has_account_status_column_and_bulk_check_button():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("unchecked@example.com.json", b"{}", "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/admin/files")

    assert page.status_code == 200
    assert "账号状态" in page.text
    assert "批量检测账号状态" in page.text
    assert 'formaction="/admin/files/account-status"' in page.text
    assert 'class="account-status-filter"' in page.text
    assert 'name="account_status"' in page.text
    assert '<option value="unchecked">未检测</option>' in page.text


def test_files_page_page_size_form_is_outside_bulk_post_form():
    reset_db()
    client = TestClient(app)
    login(client)

    page = client.get("/admin/files")

    assert page.status_code == 200
    bulk_form_start = page.text.index('class="table-form list-form files-list-form"')
    page_size_form_start = page.text.index('class="page-size-form"')
    bulk_form_end = page.text.index("</form>", bulk_form_start)
    assert bulk_form_end < page_size_form_start


def test_files_page_filters_by_account_status():
    reset_db()
    client = TestClient(app)
    login(client)
    for filename in ("unchecked@example.com.json", "usable@example.com.json", "bad@example.com.json"):
        response = client.post(
            "/admin/uploads",
            files={"file": (filename, b"{}", "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        files[1].account_status = "available"
        files[2].account_status = "unavailable"
        db.commit()

    unchecked = client.get("/admin/files?account_status=unchecked&page_size=100")
    available = client.get("/admin/files?account_status=available&page_size=100")
    unavailable = client.get("/admin/files?account_status=unavailable&page_size=100")

    assert unchecked.status_code == 200
    assert "unchecked@example.com" in unchecked.text
    assert "usable@example.com" not in unchecked.text
    assert "bad@example.com" not in unchecked.text
    assert '<option value="unchecked" selected>未检测</option>' in unchecked.text
    assert '<input type="hidden" name="account_status" value="unchecked">' in unchecked.text

    assert available.status_code == 200
    assert "usable@example.com" in available.text
    assert "unchecked@example.com" not in available.text
    assert "bad@example.com" not in available.text
    assert '<option value="available" selected>可用</option>' in available.text

    assert unavailable.status_code == 200
    assert "bad@example.com" in unavailable.text
    assert "unchecked@example.com" not in unavailable.text
    assert "usable@example.com" not in unavailable.text
    assert '<option value="unavailable" selected>不可用</option>' in unavailable.text


def test_files_page_account_status_badges_use_available_and_voided_colors():
    reset_db()
    client = TestClient(app)
    login(client)
    for filename in ("usable@example.com.json", "bad@example.com.json"):
        response = client.post(
            "/admin/uploads",
            files={"file": (filename, b"{}", "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        files[0].account_status = "available"
        files[1].account_status = "unavailable"
        db.commit()

    page = client.get("/admin/files")
    styles_css = Path("static/styles.css").read_text(encoding="utf-8")

    assert 'class="status account-available">可用</span>' in page.text
    assert 'class="status account-unavailable">不可用</span>' in page.text
    assert ".status.account-available" in styles_css
    assert ".status.account-unavailable" in styles_css


def test_admin_batch_status_does_not_reactivate_used_cards():
    reset_db()
    client = TestClient(app)
    login(client)
    for idx in range(2):
        response = client.post(
            "/admin/uploads",
            files={"file": (f"state{idx}@example.com___070323.json", b'{"ok": true}', "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        file_ids = [item.id for item in db.query(ManagedFile).order_by(ManagedFile.id).all()]
        card_ids = [item.id for item in db.query(Card).order_by(Card.id).all()]

    response = client.post("/admin/files/status", data={"ids": file_ids, "target_status": "sold"}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        assert {item.status for item in files} == {"sold"}
        assert all(item.sold_at for item in files)

    response = client.post("/admin/files/status", data={"ids": file_ids, "target_status": "available"}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).order_by(ManagedFile.id).all()
        assert {item.status for item in files} == {"available"}
        assert all(item.sold_at is None and item.voided_at is None for item in files)

    response = client.post("/admin/cards/status", data={"ids": card_ids, "target_status": "sold"}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        assert {item.status for item in cards} == {"sold"}
        assert all(item.used_at for item in cards)

    response = client.post("/admin/cards/status", data={"ids": card_ids, "target_status": "pending"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import Card

        cards = db.query(Card).order_by(Card.id).all()
        assert {item.status for item in cards} == {"sold"}
        assert all(item.used_at for item in cards)


def test_current_batch_status_routes_validate_statuses_and_ids_atomically():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("validate@example.com___070323.json", b'{"ok": true}', "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        file_id = db.query(ManagedFile).first().id
        card_id = db.query(Card).first().id

    response = client.post("/admin/files/status", data={"ids": file_id, "target_status": "bad"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import ManagedFile

        assert db.get(ManagedFile, file_id).status == "available"

    response = client.post("/admin/files/status", data={"ids": [file_id, 999999], "target_status": "sold"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import ManagedFile

        assert db.get(ManagedFile, file_id).status == "available"
        assert db.get(ManagedFile, file_id).sold_at is None

    response = client.post("/admin/cards/status", data={"ids": card_id, "target_status": "bad"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import Card

        assert db.get(Card, card_id).status == "pending"

    response = client.post("/admin/cards/status", data={"ids": [card_id, 999999], "target_status": "sold"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        from app.models import Card

        assert db.get(Card, card_id).status == "pending"
        assert db.get(Card, card_id).used_at is None


def test_current_batch_status_routes_return_json_for_fetch_requests():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("jsonstatus@example.com___070323.json", b'{"ok": true}', "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import Card, ManagedFile

        file_id = db.query(ManagedFile).first().id
        card_id = db.query(Card).first().id

    headers = {"Accept": "application/json", "X-Requested-With": "fetch"}
    response = client.post("/admin/cards/status", data={"ids": card_id, "target_status": "bad"}, headers=headers)
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "目标状态无效"}
    with SessionLocal() as db:
        from app.models import Card

        assert db.get(Card, card_id).status == "pending"

    response = client.post("/admin/cards/status", data={"ids": card_id, "target_status": "pending"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "message" in response.json()
    with SessionLocal() as db:
        from app.models import Card

        assert db.get(Card, card_id).status == "pending"

    response = client.post("/admin/files/status", data={"ids": [file_id, 999999], "target_status": "sold"}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"ok": False, "error": "部分文件不存在或无权限修改"}
    with SessionLocal() as db:
        from app.models import ManagedFile

        assert db.get(ManagedFile, file_id).status == "available"


def test_multiple_cards_redeem_as_one_cpa_zip():
    reset_db()
    client = TestClient(app)
    login(client)
    for idx in range(2):
        filename = f"multi{idx}@example.com___070323.json"
        response = client.post(
            "/admin/uploads",
            files={
                "file": (
                    filename,
                    cpa_payload(f"multi{idx}@example.com", f"acct_multi_{idx}", f"user_multi_{idx}"),
                    "application/json",
                )
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    response = client.post(
        "/admin/cards/create",
        data={"file_count": 1, "quantity": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import Card

        codes = [card.code for card in db.query(Card).order_by(Card.id).all()]

    response = client.post("/api/redeem", data={"card_code": "\n".join(codes), "output_format": "cpa"})
    assert response.status_code == 200
    assert response.json()["expires_in"] == 86400
    download = client.get(urlsplit(response.json()["download_url"]).path)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        accounts = [json.loads(archive.read(name)) for name in archive.namelist()]
    assert {account["email"] for account in accounts} == {
        "multi0@example.com",
        "multi1@example.com",
    }
    second_download = client.get(urlsplit(response.json()["download_url"]).path)
    assert second_download.status_code == 410
    assert client.get("/api/inventory").json() == {"inventory": 0}


def test_upload_accepts_any_json_filename():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("bad.json", b"{}", "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/files?")
    assert "%E5%B7%B2%E5%AF%BC%E5%85%A5+1" in response.headers["location"]
    assert client.get("/api/inventory").json() == {"inventory": 1}


def test_upload_rejects_chinese_filename_without_creating_managed_file():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("中文.json", b"{}", "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "%E6%96%87%E4%BB%B6%E5%90%8D%E4%B8%8D%E8%83%BD%E5%8C%85%E5%90%AB%E4%B8%AD%E6%96%87" in response.headers["location"]
    assert client.get("/api/inventory").json() == {"inventory": 0}

    with SessionLocal() as db:
        from app.models import ManagedFile

        assert db.query(ManagedFile).count() == 0


def test_upload_imports_valid_files_and_reports_chinese_filename_errors():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files=[
            ("file", ("ok.json", b"{}", "application/json")),
            ("file", ("中文.json", b"{}", "application/json")),
        ],
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/files?")
    assert "%E5%B7%B2%E5%AF%BC%E5%85%A5+1" in response.headers["location"]
    assert "%E6%96%87%E4%BB%B6%E5%90%8D%E4%B8%8D%E8%83%BD%E5%8C%85%E5%90%AB%E4%B8%AD%E6%96%87" in response.headers["location"]
    assert client.get("/api/inventory").json() == {"inventory": 1}

    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).all()
        assert len(files) == 1
        assert files[0].original_name == "ok"


def test_upload_same_filename_replaces_existing_file_and_timestamp():
    from datetime import datetime

    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/uploads",
        files={"file": ("same-name.json", b'{"value": 1}', "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import ManagedFile

        managed = db.query(ManagedFile).first()
        file_id = managed.id
        stored_path = managed.stored_path
        assert managed.original_name == "same-name"
        managed.uploaded_at = datetime(2020, 1, 1)
        db.commit()

    response = client.post(
        "/admin/uploads",
        files={"file": ("same-name.json", b'{"value": 2}', "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/api/inventory").json() == {"inventory": 1}

    with SessionLocal() as db:
        from app.models import ManagedFile

        files = db.query(ManagedFile).all()
        assert len(files) == 1
        assert files[0].id == file_id
        assert files[0].original_name == "same-name"
        assert files[0].stored_path == stored_path
        assert files[0].uploaded_at > datetime(2020, 1, 1)
        assert Path(files[0].stored_path).read_bytes() == b'{"value": 2}'


def test_admin_can_toggle_other_users_but_not_self():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/users",
        data={"username": "worker", "password": "secret-password-123", "role": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import User

        admin = db.query(User).filter_by(username=TEST_SUPER_USERNAME).first()
        worker = db.query(User).filter_by(username="worker").first()
        admin_id = admin.id
        worker_id = worker.id

    response = client.post(f"/admin/users/{admin_id}/toggle", follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import User

        assert db.get(User, admin_id).is_active is True

    response = client.post(f"/admin/users/{worker_id}/toggle", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    with SessionLocal() as db:
        from app.models import User

        assert db.get(User, worker_id).is_active is False


def test_admin_can_reset_user_password():
    reset_db()
    client = TestClient(app)
    login(client)
    response = client.post(
        "/admin/users",
        data={"username": "worker", "password": "secret123", "role": "user"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        from app.models import User

        worker_id = db.query(User).filter_by(username="worker").first().id

    response = client.post(
        "/admin/users/reset-password",
        data={"user_id": worker_id, "password": "newpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    other = TestClient(app)
    assert other.post("/admin/login", data={"username": "worker", "password": "secret123"}, follow_redirects=False).status_code == 401
    assert other.post("/admin/login", data={"username": "worker", "password": "newpass123"}, follow_redirects=False).status_code == 303


def test_admin_users_page_configures_each_users_quota_pool():
    reset_db()
    client = TestClient(app)
    login(client)

    response = client.post(
        "/admin/users",
        data={
            "username": "worker",
            "password": "secret-password-123",
            "role": "admin",
            "quota_pool_base_url": "http://pool3.example",
            "quota_pool_management_key": "pool-secret",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import User

        worker = db.query(User).filter_by(username="worker").first()
        assert worker.quota_pool_base_url == "http://pool3.example"
        assert worker.quota_pool_management_key == "pool-secret"
        worker_id = worker.id

    page = client.get("/admin/users")
    assert page.status_code == 200
    assert "号池地址" in page.text
    assert "管理秘钥" in page.text
    assert "配置号池" in page.text
    assert 'name="quota_pool_base_url"' in page.text
    assert 'name="quota_pool_management_key"' in page.text

    response = client.post(
        f"/admin/users/{worker_id}/pool",
        data={
            "quota_pool_base_url": "http://pool3-new.example",
            "quota_pool_management_key": "new-secret",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        from app.models import User

        worker = db.get(User, worker_id)
        assert worker.quota_pool_base_url == "http://pool3-new.example"
        assert worker.quota_pool_management_key == "new-secret"


def test_background_startup_is_the_only_documented_entrypoint():
    startup_files = {
        "README.md": Path("README.md").read_text(encoding="utf-8"),
        "run.py": Path("run.py").read_text(encoding="utf-8"),
        "start-background.bat": Path("start-background.bat").read_text(encoding="utf-8"),
    }

    assert "8000" not in "\n".join(startup_files.values())
    assert "uvicorn.run" not in startup_files["run.py"]
    assert "18743" in startup_files["start-background.bat"]
