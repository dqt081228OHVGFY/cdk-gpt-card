from datetime import datetime, timedelta
import math
import asyncio
import logging
from datetime import timezone
from pathlib import Path
import secrets
from typing import Annotated
from urllib.parse import quote_plus, unquote_plus
from urllib.parse import urlsplit
import zipfile
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.orm import Session, joinedload
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    get_admin_password,
    get_admin_username,
    get_cookie_secure,
    get_public_base_url,
    get_session_secret,
    get_super_admin_password,
    get_super_admin_username,
)
from .database import Base, SessionLocal, engine, get_db
from .database import DOWNLOAD_DIR, UPLOAD_DIR
from .health import HEALTH_ALIVE, HEALTH_CHECKING, HEALTH_DEAD, HEALTH_UNKNOWN, health_client_ready
from .models import (
    ADMIN_ROLES,
    PRODUCT_DRAFT,
    PRODUCT_HIDDEN,
    PRODUCT_LISTED,
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    AuditLog,
    Card,
    ManagedFile,
    Product,
    Redemption,
    TemporaryDownload,
    User,
)
from .rate_limit import (
    SecurityLimitError,
    begin_security_attempt,
    cleanup_security_attempts,
    finish_security_attempt,
    request_client_identifier,
)
from .security import authenticate_user, hash_password, verify_password
from .storage_crypto import read_file as read_account_file
from .services import (
    CONVERT_DOWNLOAD_TTL,
    DOWNLOAD_TTL,
    ImportBudget,
    ServiceError,
    add_audit,
    cleanup_temporary_downloads,
    convert_json_uploads,
    create_cards,
    create_temporary_download,
    default_product_fields,
    ensure_legacy_product,
    import_upload,
    import_upload_files,
    inventory_breakdown,
    json_download_name,
    lookup_card_download,
    public_delivery_products,
    product_health_used_last_24h,
    redeem_card,
    redeem_cards,
    redeem_cards_as_sub2api,
    rebuild_redemption_download,
    resolve_temporary_download,
    run_file_health_checks_parallel,
    void_cards,
    void_files,
)


logger = logging.getLogger(__name__)
app = FastAPI(title="GPT发卡网")
app.mount("/static", StaticFiles(directory="static"), name="static")
ADMIN_CSRF_SESSION_KEY = "admin_csrf_token"
ADMIN_CSRF_COOKIE = "admin_csrf"
CONVERT_SEMAPHORE = asyncio.Semaphore(1)
UPLOAD_SEMAPHORE = asyncio.Semaphore(1)
REDEEM_SEMAPHORE = asyncio.Semaphore(1)


def admin_csrf_context(request: Request) -> dict[str, str]:
    token = request.session.get(ADMIN_CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[ADMIN_CSRF_SESSION_KEY] = token
    return {"csrf_token": token}


templates = Jinja2Templates(directory="templates", context_processors=[admin_csrf_context])
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def normalized_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return scheme, hostname, port or (443 if scheme == "https" else 80)


def urlencoded_form_value(raw_body: bytes, field_name: str) -> str | None:
    """Read one URL-encoded field without imposing a limit on unrelated fields."""
    if len(raw_body) > 256 * 1024:
        return None
    try:
        for field in raw_body.split(b"&"):
            key, separator, value = field.partition(b"=")
            if separator and unquote_plus(key.decode("ascii")) == field_name:
                return unquote_plus(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return None


@app.middleware("http")
async def reject_cross_origin_admin_posts(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/admin/"):
        origin = request.headers.get("origin")
        expected_token = request.session.get(ADMIN_CSRF_SESSION_KEY, "")
        supplied_token = request.headers.get("x-csrf-token")
        if not supplied_token and origin:
            content_type = request.headers.get("content-type", "").lower()
            if content_type.startswith("application/x-www-form-urlencoded"):
                raw_body = await request.body()
                supplied_token = urlencoded_form_value(raw_body, "csrf_token")
            elif origin.strip().lower() == "null" and content_type.startswith("multipart/form-data"):
                supplied_token = request.cookies.get(ADMIN_CSRF_COOKIE, "")
        if origin:
            request_origin = normalized_origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
            allowed_origins = {
                item
                for item in (normalized_origin(get_public_base_url()), request_origin)
                if item is not None
            }
            if normalized_origin(origin) not in allowed_origins:
                csrf_valid = (
                    isinstance(expected_token, str)
                    and bool(expected_token)
                    and isinstance(supplied_token, str)
                    and secrets.compare_digest(supplied_token, expected_token)
                )
                if not csrf_valid:
                    return JSONResponse({"detail": "拒绝跨站管理请求"}, status_code=403)
        elif request.url.path != "/admin/login":
            cookie_token = request.cookies.get(ADMIN_CSRF_COOKIE, "")
            csrf_valid = (
                isinstance(expected_token, str)
                and bool(expected_token)
                and bool(cookie_token)
                and secrets.compare_digest(cookie_token, expected_token)
            )
            if not csrf_valid:
                return JSONResponse({"detail": "拒绝缺少来源验证的管理请求"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
        "frame-src 'self' https:; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com; "
        "connect-src 'self' https://cloudflareinsights.com",
    )
    csrf_token = request.session.get(ADMIN_CSRF_SESSION_KEY)
    if request.url.path.startswith("/admin") and isinstance(csrf_token, str) and csrf_token:
        response.set_cookie(
            ADMIN_CSRF_COOKIE,
            csrf_token,
            max_age=8 * 60 * 60,
            httponly=True,
            secure=get_cookie_secure(),
            samesite="strict",
        )
    return response


def format_dt(value: datetime | None, empty: str = "-") -> str:
    if not value:
        return empty
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_full_dt(value: datetime | None, empty: str = "-") -> str:
    if not value:
        return empty
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return utc_value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def status_label(value: str) -> str:
    return {
        "available": "可用",
        "locked": "锁定中",
        "pending": "待使用",
        "sold": "已使用",
        "voided": "已作废",
    }.get(value, value)


def account_status_label(value: str | None) -> str:
    return {
        "available": "活",
        "unavailable": "死",
        "unknown": "暂时未知",
        "checking": "检测中",
    }.get(value or "", "未检测")


def card_status_label(value: str) -> str:
    return {
        "available": "可使用",
        "pending": "可使用",
        "sold": "已使用",
        "voided": "已作废",
    }.get(value, value)


def product_status_label(value: str) -> str:
    return {
        PRODUCT_DRAFT: "草稿",
        PRODUCT_LISTED: "已上架",
        PRODUCT_HIDDEN: "已隐藏",
    }.get(value, value)


templates.env.filters["dt"] = format_dt
templates.env.filters["full_dt"] = format_full_dt
templates.env.filters["status_label"] = status_label
templates.env.filters["account_status_label"] = account_status_label
templates.env.filters["card_status_label"] = card_status_label
templates.env.filters["product_status_label"] = product_status_label
PAGE_SIZES = (50, 100, 200)
FILE_STATUSES = {"available", "locked", "sold", "voided"}
CARD_STATUSES = {"pending", "sold", "voided"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_MANUAL_JSON_BYTES = 2 * 1024 * 1024
MAX_CONVERT_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_FILES = 100
REQUEST_BODY_LIMITS = {
    "/api/convert": MAX_CONVERT_BYTES + 1024 * 1024,
    "/admin/uploads": MAX_UPLOAD_BYTES + 1024 * 1024,
    "/admin/uploads/manual": MAX_MANUAL_JSON_BYTES + 64 * 1024,
    "/admin/liveness/upload-check": MAX_UPLOAD_BYTES + 1024 * 1024,
}
EARLY_ADMIN_UPLOAD_ROLES = {
    "/admin/uploads": ADMIN_ROLES,
    "/admin/uploads/manual": ADMIN_ROLES,
    "/admin/liveness/upload-check": ADMIN_ROLES,
}
SENSITIVE_DOWNLOAD_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
_next_cleanup_at = datetime.min
_next_liveness_sync_at = datetime.min
_liveness_sync_task: asyncio.Task | None = None
_upload_liveness_task: asyncio.Task | None = None
_upload_liveness_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue(maxsize=500)
LIVENESS_WORKER_SEMAPHORE = asyncio.Semaphore(1)
LIVENESS_SYNC_INTERVAL = timedelta(minutes=15)
LIVENESS_SYNC_LIMIT_PER_USER = 50
UPLOAD_LIVENESS_BATCH_SIZE = 20
UPLOAD_LIVENESS_BATCH_INTERVAL_SECONDS = 60


async def read_upload_batch(
    uploads: list[UploadFile],
    max_bytes: int,
    limit_error: str,
) -> list[tuple[UploadFile, bytes]]:
    if len(uploads) > MAX_UPLOAD_FILES:
        raise ServiceError(f"单批文件数量不能超过 {MAX_UPLOAD_FILES}")
    buffered: list[tuple[UploadFile, bytes]] = []
    total_bytes = 0
    for upload in uploads:
        chunks: list[bytes] = []
        while True:
            remaining = max_bytes - total_bytes
            chunk = await upload.read(min(1024 * 1024, remaining + 1))
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise ServiceError(limit_error)
            chunks.append(chunk)
        buffered.append((upload, b"".join(chunks)))
    return buffered


@app.middleware("http")
async def reject_oversized_request_bodies(request: Request, call_next):
    path = request.url.path
    if request.method == "POST" and path in REQUEST_BODY_LIMITS:
        content_length = request.headers.get("content-length")
        if not content_length:
            return JSONResponse({"detail": "上传请求必须提供 Content-Length"}, status_code=411)
        try:
            body_size = int(content_length)
        except ValueError:
            return JSONResponse({"detail": "Content-Length 无效"}, status_code=400)
        if body_size < 0 or body_size > REQUEST_BODY_LIMITS[path]:
            return JSONResponse({"detail": "上传内容超过服务器资源限制"}, status_code=413)

        allowed_roles = EARLY_ADMIN_UPLOAD_ROLES.get(path)
        if allowed_roles is not None:
            user_id = request.session.get("user_id")
            session_version = request.session.get("session_version")
            with SessionLocal() as early_db:
                user = early_db.get(User, int(user_id)) if user_id else None
                authorized = bool(
                    user
                    and user.is_active
                    and user.role in allowed_roles
                    and user.session_version == session_version
                )
            if not authorized:
                return RedirectResponse("/admin/login", status_code=303)
    return await call_next(request)


@app.middleware("http")
async def periodic_cleanup(request: Request, call_next):
    global _next_cleanup_at, _next_liveness_sync_at, _liveness_sync_task
    timestamp = datetime.utcnow()
    if timestamp >= _next_cleanup_at:
        _next_cleanup_at = timestamp + timedelta(minutes=10)
        try:
            with SessionLocal() as maintenance_db:
                cleanup_temporary_downloads(maintenance_db, timestamp)
                cleanup_security_attempts(maintenance_db, timestamp)
        except Exception:
            logger.exception("Periodic temporary-file cleanup failed")
    if timestamp >= _next_liveness_sync_at and (_liveness_sync_task is None or _liveness_sync_task.done()):
        _next_liveness_sync_at = timestamp + LIVENESS_SYNC_INTERVAL

        async def run_background_liveness_sync() -> None:
            try:
                with SessionLocal() as maintenance_db:
                    await sync_liveness_statuses(maintenance_db, datetime.utcnow())
            except Exception:
                logger.exception("Periodic account liveness sync failed")

        _liveness_sync_task = asyncio.create_task(run_background_liveness_sync())
    return await call_next(request)


# Register sessions after function middleware so request.session is available
# to origin and CSRF checks that wrap every admin request.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    same_site="strict",
    https_only=get_cookie_secure(),
    max_age=8 * 60 * 60,
)


def normalize_page_size(page_size: int | None) -> int:
    return page_size if page_size in PAGE_SIZES else 50


def normalize_page(page: int | None) -> int:
    return page if page and page > 0 else 1


def pagination_context(total: int, page: int, page_size: int) -> dict[str, int | bool | tuple[int, ...]]:
    total_pages = max(1, math.ceil(total / page_size))
    page = min(page, total_pages)
    return {
        "page": page,
        "page_size": page_size,
        "page_sizes": PAGE_SIZES,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
    }


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        datetime_type = "DATETIME" if connection.dialect.name == "sqlite" else "TIMESTAMP"
        integer_type = "INTEGER"
        columns = {column["name"] for column in inspect(connection).get_columns("users")}
        if "updated_at" not in columns:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN updated_at {datetime_type}"))
            connection.execute(text("UPDATE users SET updated_at = created_at WHERE updated_at IS NULL"))
        if "session_version" not in columns:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN session_version {integer_type} NOT NULL DEFAULT 0"))
        if "quota_pool_base_url" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN quota_pool_base_url VARCHAR(500) DEFAULT ''"))
        if "quota_pool_management_key" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN quota_pool_management_key TEXT DEFAULT ''"))
        if "liveness_pool_base_url" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN liveness_pool_base_url VARCHAR(500) DEFAULT ''"))
        if "liveness_pool_management_key" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN liveness_pool_management_key TEXT DEFAULT ''"))
        if "liveness_last_sync_at" not in columns:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN liveness_last_sync_at {datetime_type}"))
        connection.execute(
            text(
                """
                UPDATE users
                SET liveness_pool_base_url = quota_pool_base_url
                WHERE COALESCE(liveness_pool_base_url, '') = ''
                  AND COALESCE(quota_pool_base_url, '') != ''
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE users
                SET liveness_pool_management_key = quota_pool_management_key
                WHERE COALESCE(liveness_pool_management_key, '') = ''
                  AND COALESCE(quota_pool_management_key, '') != ''
                """
            )
        )
        file_columns = {column["name"] for column in inspect(connection).get_columns("files")}
        if "sold_card_id" not in file_columns:
            connection.execute(text(f"ALTER TABLE files ADD COLUMN sold_card_id {integer_type}"))
        added_account_status = "account_status" not in file_columns
        if added_account_status:
            connection.execute(text("ALTER TABLE files ADD COLUMN account_status VARCHAR(20)"))
        if "account_checked_at" not in file_columns:
            connection.execute(text(f"ALTER TABLE files ADD COLUMN account_checked_at {datetime_type}"))
        if "account_error" not in file_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN account_error TEXT"))
        if "account_error_label" not in file_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN account_error_label VARCHAR(255)"))
        if "source_format" not in file_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN source_format VARCHAR(20) DEFAULT 'cpa'"))
        if "account_email" not in file_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN account_email VARCHAR(320)"))
        if "product_id" not in file_columns:
            connection.execute(text(f"ALTER TABLE files ADD COLUMN product_id {integer_type}"))
        if added_account_status:
            connection.execute(
                text(
                    """
                    UPDATE files
                    SET account_status = 'available'
                    WHERE status = 'available'
                      AND (account_status IS NULL OR account_status = '')
                    """
                )
            )
        card_columns = {column["name"] for column in inspect(connection).get_columns("cards")}
        added_redemption_count = "redemption_count" not in card_columns
        if "max_redemptions" not in card_columns:
            connection.execute(text(f"ALTER TABLE cards ADD COLUMN max_redemptions {integer_type} NOT NULL DEFAULT 1"))
        if added_redemption_count:
            connection.execute(text(f"ALTER TABLE cards ADD COLUMN redemption_count {integer_type} NOT NULL DEFAULT 0"))
        if "expires_at" not in card_columns:
            connection.execute(text(f"ALTER TABLE cards ADD COLUMN expires_at {datetime_type}"))
        if "product_id" not in card_columns:
            connection.execute(text(f"ALTER TABLE cards ADD COLUMN product_id {integer_type}"))
        if added_redemption_count:
            connection.execute(
                text(
                    "UPDATE cards SET redemption_count = "
                    "(SELECT COUNT(*) FROM redemptions WHERE redemptions.card_id = cards.id)"
                )
            )
            connection.execute(text("UPDATE cards SET redemption_count = 1 WHERE status = 'sold' AND redemption_count = 0"))
        redemption_columns = {column["name"] for column in inspect(connection).get_columns("redemptions")}
        if "product_id" not in redemption_columns:
            connection.execute(text(f"ALTER TABLE redemptions ADD COLUMN product_id {integer_type}"))
        if "output_format" not in redemption_columns:
            connection.execute(text("ALTER TABLE redemptions ADD COLUMN output_format VARCHAR(20)"))
            connection.execute(
                text(
                    """
                    UPDATE redemptions
                    SET output_format = CASE
                        WHEN download_path LIKE '%sub2api%' THEN 'sub'
                        ELSE 'cpa'
                    END
                    WHERE output_format IS NULL
                    """
                )
            )
        product_columns = {column["name"] for column in inspect(connection).get_columns("products")}
        if "health_timeout_seconds" not in product_columns:
            connection.execute(text(f"ALTER TABLE products ADD COLUMN health_timeout_seconds {integer_type} NOT NULL DEFAULT 15"))
        if "health_daily_limit" not in product_columns:
            connection.execute(text(f"ALTER TABLE products ADD COLUMN health_daily_limit {integer_type} NOT NULL DEFAULT 0"))
        connection.execute(text("UPDATE cards SET status = 'pending' WHERE status IN ('unused', 'listed', 'available')"))
        connection.execute(text("UPDATE cards SET status = 'sold' WHERE status = 'used'"))
    with SessionLocal() as db:
        super_admin_username = get_super_admin_username()
        super_admin_password = get_super_admin_password()
        super_admin = None
        if super_admin_username:
            super_admin = db.scalar(select(User).where(User.username == super_admin_username))
        if super_admin_username and not super_admin and super_admin_password:
            db.add(
                User(
                    username=super_admin_username,
                    password_hash=hash_password(super_admin_password),
                    role=ROLE_SUPER_ADMIN,
                    is_active=True,
                )
            )
        elif super_admin:
            if super_admin.role != ROLE_SUPER_ADMIN or not super_admin.is_active:
                super_admin.role = ROLE_SUPER_ADMIN
                super_admin.is_active = True
                super_admin.updated_at = datetime.utcnow()
        admin_username = get_admin_username()
        admin_password = get_admin_password()
        if admin_username and admin_password and admin_username != super_admin_username:
            admin = db.scalar(select(User).where(User.username == admin_username))
            if not admin:
                db.add(
                    User(
                        username=admin_username,
                        password_hash=hash_password(admin_password),
                        role=ROLE_ADMIN,
                        is_active=True,
                    )
                )
        elif not admin_username:
            legacy_admin = db.scalar(select(User).where(User.username == "admin", User.role == ROLE_ADMIN))
            if legacy_admin and verify_password("Wgs0405java", legacy_admin.password_hash):
                legacy_admin.is_active = False
                legacy_admin.updated_at = datetime.utcnow()
        db.flush()
        creator_id = db.scalar(select(User.id).where(User.role == ROLE_SUPER_ADMIN).order_by(User.id.asc()))
        legacy_product = ensure_legacy_product(db, creator_id)
        db.flush()
        db.execute(text("UPDATE files SET product_id = :product_id WHERE product_id IS NULL"), {"product_id": legacy_product.id})
        db.execute(text("UPDATE cards SET product_id = :product_id WHERE product_id IS NULL"), {"product_id": legacy_product.id})
        db.execute(
            text(
                """
                UPDATE redemptions
                SET product_id = (
                    SELECT cards.product_id FROM cards WHERE cards.id = redemptions.card_id
                )
                WHERE product_id IS NULL
                """
            )
        )
        db.execute(text("UPDATE redemptions SET product_id = :product_id WHERE product_id IS NULL"), {"product_id": legacy_product.id})
        db.commit()
        cleanup_temporary_downloads(db)
        cleanup_security_attempts(db)


@app.on_event("startup")
async def resume_pending_upload_liveness() -> None:
    with SessionLocal() as db:
        pending = list(
            db.execute(
                select(ManagedFile.uploader_id, ManagedFile.id)
                .join(Product, ManagedFile.product_id == Product.id)
                .where(
                    ManagedFile.status == "available",
                    Product.health_check_enabled.is_(True),
                    or_(
                        ManagedFile.account_status.is_(None),
                        ManagedFile.account_status == "",
                        (ManagedFile.account_status == "available") & ManagedFile.account_checked_at.is_(None),
                    ),
                )
                .order_by(ManagedFile.uploaded_at.asc(), ManagedFile.id.asc())
                .limit(500)
            ).all()
        )
        files_by_actor: dict[int, list[int]] = {}
        for actor_id, file_id in pending:
            files_by_actor.setdefault(actor_id, []).append(file_id)
        for actor_id, file_ids in files_by_actor.items():
            schedule_uploaded_liveness(db, actor_id, file_ids)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if not user or request.session.get("session_version") != user.session_version:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user or not user.is_active or user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return user


def require_admin(current_user: User = Depends(require_user)) -> User:
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def require_super_admin(current_user: User = Depends(require_admin)) -> User:
    if current_user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return current_user


def scope_managed_files(query, current_user: User):
    return query


def scope_cards(query, current_user: User):
    return query


def scope_redemptions(query, current_user: User):
    return query


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def message_url(path: str, message: str | None = None, error: str | None = None) -> str:
    if message:
        return f"{path}?message={quote_plus(message)}"
    if error:
        return f"{path}?error={quote_plus(error)}"
    return path


def public_download_url(token: str) -> str:
    return f"{get_public_base_url()}/d/{token}"


def remove_temporary_file(path_value: str) -> None:
    try:
        path = Path(path_value).resolve()
        if path.parent == DOWNLOAD_DIR.resolve() and path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("failed to remove temporary download file", exc_info=True)


def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "fetch"


def action_result(
    request: Request,
    path: str,
    message: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> JSONResponse | RedirectResponse:
    if wants_json(request):
        payload = {"ok": error is None}
        if message:
            payload["message"] = message
        if error:
            payload["error"] = error
        return JSONResponse(payload, status_code=status_code)
    return redirect(message_url(path, message=message, error=error))


def parse_date(value: str | None, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        base = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return base + timedelta(days=1) if end else base


def parse_card_expiration(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        local_value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ServiceError("过期时间格式无效") from exc
    if local_value.tzinfo is None:
        local_value = local_value.replace(tzinfo=DISPLAY_TIMEZONE)
    return local_value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_card_policy(max_redemptions: int, expires_at: datetime | None) -> None:
    if max_redemptions < 1 or max_redemptions > 100:
        raise ServiceError("使用次数必须在 1 到 100 之间")
    if expires_at is not None and expires_at <= datetime.utcnow():
        raise ServiceError("过期时间必须晚于当前时间")


def apply_file_status(item: ManagedFile, target_status: str, timestamp: datetime) -> None:
    item.status = target_status
    if target_status in {"available", "locked"}:
        item.sold_at = None
        item.voided_at = None
        item.sold_card_id = None
    elif target_status == "sold":
        item.sold_at = item.sold_at or timestamp
        item.voided_at = None
    elif target_status == "voided":
        item.sold_at = None
        item.sold_card_id = None
        item.voided_at = item.voided_at or timestamp


def liveness_pool_config(user: User | None) -> tuple[str, str]:
    if not user:
        return "", ""
    return (user.liveness_pool_base_url or "").strip(), (user.liveness_pool_management_key or "").strip()


def liveness_pool_owner(db: Session) -> User | None:
    configured_owner = db.scalar(
        select(User)
        .where(
            User.role == ROLE_SUPER_ADMIN,
            User.is_active.is_(True),
            User.liveness_pool_base_url != "",
            User.liveness_pool_management_key != "",
        )
        .order_by(User.id.asc())
    )
    if configured_owner:
        return configured_owner
    return db.scalar(
        select(User)
        .where(User.role == ROLE_SUPER_ADMIN, User.is_active.is_(True))
        .order_by(User.id.asc())
    )


def liveness_pool_config_for_db(db: Session) -> tuple[User | None, str, str]:
    owner = liveness_pool_owner(db)
    base_url, management_key = liveness_pool_config(owner)
    return owner, base_url, management_key


async def run_liveness_checks_for_files(
    db: Session,
    actor: User,
    files: list[ManagedFile],
    timestamp: datetime | None = None,
    audit_action: str = "check_file_account_status",
) -> dict[str, int | str]:
    if len(files) > 50:
        raise ServiceError("单次最多检测 50 个文件")

    timestamp = timestamp or datetime.utcnow()
    outcomes = await asyncio.to_thread(
        run_file_health_checks_parallel,
        [item.id for item in files],
        actor.id,
        audit_action,
        timestamp,
        4,
        True,
    )
    db.expire_all()
    available = 0
    unavailable = 0
    unknown = 0
    for outcome in outcomes:
        status = outcome.status
        if status == HEALTH_ALIVE:
            available += 1
        elif status == HEALTH_DEAD:
            unavailable += 1
        elif status == HEALTH_UNKNOWN:
            unknown += 1

    message = f"已检测 {len(files)} 个文件：活 {available} 个，死 {unavailable} 个，暂时未知 {unknown} 个"
    return {
        "checked": len(files),
        "available": available,
        "unavailable": unavailable,
        "unknown": unknown,
        "message": message,
    }


async def sync_liveness_statuses(db: Session, timestamp: datetime) -> tuple[int, int, int]:
    users = list(
        db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_(ADMIN_ROLES),
            )
        )
    )
    available = 0
    unavailable = 0
    checked = 0
    stale_before = timestamp - LIVENESS_SYNC_INTERVAL
    for user in users:
        files = list(
            db.scalars(
                select(ManagedFile)
                .join(Product, ManagedFile.product_id == Product.id)
                .where(
                    ManagedFile.uploader_id == user.id,
                    ManagedFile.status == "available",
                    Product.health_check_enabled.is_(True),
                    (ManagedFile.account_checked_at.is_(None)) | (ManagedFile.account_checked_at <= stale_before),
                )
                .order_by(ManagedFile.account_checked_at.asc().nullsfirst(), ManagedFile.id.asc())
                .limit(LIVENESS_SYNC_LIMIT_PER_USER)
            )
        )
        if not files:
            user.liveness_last_sync_at = timestamp
            db.commit()
            continue
        outcomes = await asyncio.to_thread(
            run_file_health_checks_parallel,
            [item.id for item in files],
            None,
            "auto_liveness_sync",
            timestamp,
            4,
            True,
        )
        db.expire_all()
        for outcome in outcomes:
            status = outcome.status
            if status == HEALTH_ALIVE:
                available += 1
            elif status == HEALTH_DEAD:
                unavailable += 1
            if status in {HEALTH_ALIVE, HEALTH_DEAD, HEALTH_UNKNOWN}:
                checked += 1
        user.liveness_last_sync_at = timestamp
        db.commit()
    return checked, available, unavailable


def clear_file_liveness_state(item: ManagedFile) -> None:
    item.account_status = None
    item.account_checked_at = None
    item.account_error = ""
    item.account_error_label = ""


async def remove_redeemed_files_from_liveness_pool(download_path: str) -> None:
    try:
        with SessionLocal() as worker_db:
            redemptions = list(
                worker_db.scalars(select(Redemption).where(Redemption.download_path == download_path))
            )
            file_ids = {
                int(value)
                for redemption in redemptions
                for value in (redemption.file_ids or "").split(",")
                if value.strip().isdigit()
            }
            if not file_ids:
                return
            files = list(
                worker_db.scalars(
                    select(ManagedFile).where(
                        ManagedFile.id.in_(file_ids),
                        ManagedFile.status == "sold",
                    )
                )
            )
            for item in files:
                clear_file_liveness_state(item)
                add_audit(
                    worker_db,
                    None,
                    "clear_redeemed_liveness_state",
                    "file",
                    item.id,
                    item.original_name,
                )
            worker_db.commit()
    except Exception:
        logger.exception("Immediate redeemed-account liveness cleanup failed")


async def drain_uploaded_liveness_queue() -> None:
    global _upload_liveness_task
    try:
        while True:
            batch: list[tuple[int, int]] = []
            while len(batch) < UPLOAD_LIVENESS_BATCH_SIZE:
                try:
                    batch.append(_upload_liveness_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch:
                return

            files_by_actor: dict[int, list[int]] = {}
            for queued_actor_id, file_id in batch:
                files_by_actor.setdefault(queued_actor_id, []).append(file_id)
            try:
                async with LIVENESS_WORKER_SEMAPHORE:
                    for actor_id, queued_file_ids in files_by_actor.items():
                        file_ids = list(dict.fromkeys(queued_file_ids))
                        with SessionLocal() as worker_db:
                            actor = worker_db.get(User, actor_id)
                            files = list(
                                worker_db.scalars(
                                    select(ManagedFile)
                                    .where(ManagedFile.id.in_(file_ids), ManagedFile.status == "available")
                                    .order_by(ManagedFile.id.asc())
                                )
                            )
                            if actor and files:
                                await run_liveness_checks_for_files(
                                    worker_db,
                                    actor,
                                    files,
                                    audit_action="automatic_upload_liveness",
                                )
                                worker_db.commit()
            except ServiceError as exc:
                logger.warning("Automatic upload liveness skipped: %s", exc)
            except Exception:
                logger.exception("Automatic upload liveness failed")
            finally:
                for _ in batch:
                    _upload_liveness_queue.task_done()
            if not _upload_liveness_queue.empty():
                await asyncio.sleep(UPLOAD_LIVENESS_BATCH_INTERVAL_SECONDS)
    finally:
        _upload_liveness_task = None
        if not _upload_liveness_queue.empty():
            _upload_liveness_task = asyncio.create_task(drain_uploaded_liveness_queue())


def schedule_uploaded_liveness(db: Session, actor_id: int, file_ids: list[int]) -> int:
    global _upload_liveness_task
    if not file_ids:
        return 0
    enabled_ids = {
        file_id
        for file_id in db.scalars(
            select(ManagedFile.id)
            .join(Product, ManagedFile.product_id == Product.id)
            .where(
                ManagedFile.id.in_(file_ids),
                ManagedFile.status == "available",
                Product.health_check_enabled.is_(True),
            )
        )
    }
    queued = 0
    for file_id in dict.fromkeys(file_ids):
        if file_id not in enabled_ids:
            continue
        try:
            _upload_liveness_queue.put_nowait((actor_id, file_id))
            queued += 1
        except asyncio.QueueFull:
            logger.warning("Automatic upload liveness queue is full; periodic sync will handle remaining files")
            break
    if queued and (_upload_liveness_task is None or _upload_liveness_task.done()):
        _upload_liveness_task = asyncio.create_task(drain_uploaded_liveness_queue())
    return queued


def apply_card_status(card: Card, target_status: str, timestamp: datetime) -> None:
    card.status = target_status
    if target_status == "pending":
        card.used_at = None
        card.voided_at = None
    elif target_status == "sold":
        card.used_at = card.used_at or timestamp
        card.voided_at = None
    elif target_status == "voided":
        card.used_at = None
        card.voided_at = card.voided_at or timestamp


def redemption_format_label(value: str | None, download_path: str | None = None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized and download_path and "sub2api" in download_path.lower():
        normalized = "sub"
    if normalized == "sub":
        return "SUB 文件"
    return "CPA 文件"


def redemption_file_count(file_ids: str | None) -> int:
    return len([item for item in (file_ids or "").split(",") if item.strip()])


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "inventory": inventory_breakdown(db),
            "delivery_products": public_delivery_products(db),
        },
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/convert", response_class=HTMLResponse)
def convert_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "convert.html", {"request": request})


@app.get("/lookup", response_class=HTMLResponse)
def lookup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "lookup.html", {"request": request})


@app.get("/api/inventory")
def api_inventory(db: Session = Depends(get_db)) -> dict[str, object]:
    groups = inventory_breakdown(db)
    return {
        "inventory": groups["normal"],
        "normal": groups["normal"],
        "healthy": groups["healthy"],
        "problem": groups["problem"],
        "unchecked": groups["unchecked"],
        "total": groups["total"],
        "products": public_delivery_products(db),
    }


def build_redeem_output(card_code: str, output_format: str) -> tuple[Path, str | None]:
    with SessionLocal() as worker_db:
        if output_format == "sub":
            output_path, download_name = redeem_cards_as_sub2api(worker_db, card_code)
            return output_path, download_name
        return redeem_cards(worker_db, card_code), None


@app.post("/api/redeem")
async def api_redeem(
    request: Request,
    card_code: Annotated[str, Form()],
    output_format: Annotated[str, Form()] = "cpa",
    delivery: Annotated[str, Form()] = "link",
    db: Session = Depends(get_db),
):
    client_identifier = request_client_identifier(request)
    subject = card_code.strip().upper()[:80]
    try:
        generate_attempt_id = begin_security_attempt(db, "redeem_generate", client_identifier)
    except SecurityLimitError as exc:
        return JSONResponse(
            {"ok": False, "error": "生成次数过多，请 1 分钟后再试"},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    try:
        attempt_id = begin_security_attempt(db, "redeem", client_identifier, subject)
    except SecurityLimitError as exc:
        finish_security_attempt(db, generate_attempt_id, False, str(exc))
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )

    output_format = output_format.strip().lower()
    if output_format not in {"cpa", "sub"}:
        error = "输出格式必须是 CPA 或 SUB"
        finish_security_attempt(db, generate_attempt_id, False, error)
        finish_security_attempt(db, attempt_id, False, error)
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    try:
        await asyncio.wait_for(REDEEM_SEMAPHORE.acquire(), timeout=3)
    except TimeoutError:
        error = "兑换任务繁忙，请稍后重试"
        finish_security_attempt(db, generate_attempt_id, False, "redemption_capacity_exhausted")
        finish_security_attempt(db, attempt_id, False, "redemption_capacity_exhausted")
        return JSONResponse({"ok": False, "error": error}, status_code=503, headers={"Retry-After": "3"})

    try:
        output_path, worker_download_name = await asyncio.to_thread(build_redeem_output, card_code, output_format)
    except ServiceError as exc:
        db.rollback()
        finish_security_attempt(db, generate_attempt_id, False, str(exc))
        finish_security_attempt(db, attempt_id, False, str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Redemption worker failed")
        db.rollback()
        finish_security_attempt(db, generate_attempt_id, False, "redemption_worker_failed")
        finish_security_attempt(db, attempt_id, False, "redemption_worker_failed")
        return JSONResponse({"ok": False, "error": "兑换任务处理失败，请稍后重试"}, status_code=503)
    finally:
        REDEEM_SEMAPHORE.release()

    db.expire_all()
    output_path = Path(output_path)
    download_name = worker_download_name or ""
    media_type: str
    if output_format == "sub":
        media_type = "application/zip" if output_path.suffix.lower() == ".zip" else "application/json"
    else:
        media_type = "application/json" if output_path.suffix.lower() == ".json" else "application/zip"
        if media_type == "application/json":
            matching_redemptions = list(db.scalars(
                select(Redemption).where(Redemption.download_path == str(output_path)).order_by(Redemption.id.desc())
            ))
            redeemed_file_ids = [
                int(item)
                for redemption_item in matching_redemptions
                for item in (redemption_item.file_ids or "").split(",")
                if item.isdigit()
            ]
            first_id = redeemed_file_ids[0] if redeemed_file_ids else None
            managed = db.get(ManagedFile, first_id) if first_id else None
            if len(redeemed_file_ids) == 1 and managed:
                download_name = json_download_name(managed.original_name)
            else:
                download_name = f"cpa-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{len(redeemed_file_ids)}-accounts.json"
        else:
            matching_redemptions = list(db.scalars(
                select(Redemption).where(Redemption.download_path == str(output_path)).order_by(Redemption.id.desc())
            ))
            redeemed_file_count = sum(
                1
                for redemption_item in matching_redemptions
                for item in (redemption_item.file_ids or "").split(",")
                if item.isdigit()
            )
            download_name = f"cpa-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{redeemed_file_count or 1}-accounts.zip"

    redemption = db.scalar(
        select(Redemption).where(Redemption.download_path == str(output_path)).order_by(Redemption.id.desc())
    )
    token, link = create_temporary_download(
        db,
        output_path,
        download_name,
        media_type,
        "redeem",
        redemption.id if redemption else None,
    )
    finish_security_attempt(db, generate_attempt_id, True, output_format)
    finish_security_attempt(db, attempt_id, True, output_format)
    return JSONResponse(
        {
            "ok": True,
            "download_url": public_download_url(token),
            "filename": download_name,
            "expires_at": f"{link.expires_at.isoformat()}Z",
            "expires_in": int(DOWNLOAD_TTL.total_seconds()),
        },
        headers=SENSITIVE_DOWNLOAD_HEADERS,
        background=BackgroundTask(remove_redeemed_files_from_liveness_pool, str(output_path)),
    )


@app.post("/api/lookup")
async def api_lookup(
    request: Request,
    card_code: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    client_identifier = request_client_identifier(request)
    subject = card_code.strip().lower()[:80]
    try:
        attempt_id = begin_security_attempt(db, "lookup", client_identifier, subject)
    except SecurityLimitError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    try:
        cleanup_temporary_downloads(db)
        output_path, download_name, media_type, redemption = lookup_card_download(db, card_code)
        token, link = create_temporary_download(
            db,
            output_path,
            download_name,
            media_type,
            "lookup",
            redemption.id,
        )
    except ServiceError as exc:
        db.rollback()
        finish_security_attempt(db, attempt_id, False, str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finish_security_attempt(db, attempt_id, True, "original_file")
    return JSONResponse(
        {
            "ok": True,
            "download_url": public_download_url(token),
            "filename": download_name,
            "expires_at": f"{link.expires_at.isoformat()}Z",
            "expires_in": int(DOWNLOAD_TTL.total_seconds()),
        },
        headers=SENSITIVE_DOWNLOAD_HEADERS,
    )


@app.post("/api/convert")
async def api_convert(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    target_format: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    target_format = target_format.strip().lower()
    client_identifier = request_client_identifier(request)
    try:
        attempt_id = begin_security_attempt(db, "convert", client_identifier, target_format)
    except SecurityLimitError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )

    try:
        try:
            await asyncio.wait_for(CONVERT_SEMAPHORE.acquire(), timeout=5)
        except TimeoutError:
            finish_security_attempt(db, attempt_id, False, "conversion_capacity_exhausted")
            return JSONResponse(
                {"ok": False, "error": "转换任务繁忙，请稍后重试"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        try:
            buffered = await read_upload_batch(files, MAX_CONVERT_BYTES, "单次转换上传不能超过 20MB")
            uploads = [(upload.filename or "upload.json", raw) for upload, raw in buffered]
            cleanup_temporary_downloads(db)
            conversion = await asyncio.to_thread(convert_json_uploads, uploads, target_format)
            output_path, download_name, media_type, source_format, account_count = conversion
            token, link = create_temporary_download(db, output_path, download_name, media_type, "convert")
        finally:
            CONVERT_SEMAPHORE.release()
    except ServiceError as exc:
        db.rollback()
        finish_security_attempt(db, attempt_id, False, str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    finish_security_attempt(db, attempt_id, True, f"{source_format}:{account_count}")
    return JSONResponse(
        {
            "ok": True,
            "download_url": public_download_url(token),
            "filename": download_name,
            "source_format": source_format,
            "target_format": target_format.upper(),
            "account_count": account_count,
            "expires_at": f"{link.expires_at.isoformat()}Z",
            "expires_in": int(CONVERT_DOWNLOAD_TTL.total_seconds()),
        },
        headers=SENSITIVE_DOWNLOAD_HEADERS,
    )


@app.get("/d/{token}", name="temporary_download")
def temporary_download(token: str, db: Session = Depends(get_db)):
    try:
        link = resolve_temporary_download(db, token)
    except ServiceError as exc:
        return HTMLResponse(
            f"<main style='font-family:sans-serif;padding:48px'><h1>链接已失效</h1><p>{str(exc)}</p><a href='/'>返回首页</a></main>",
            status_code=410,
        )
    return FileResponse(
        link.file_path,
        media_type=link.media_type,
        filename=link.download_name,
        headers=SENSITIVE_DOWNLOAD_HEADERS,
        background=BackgroundTask(remove_temporary_file, link.file_path),
    )


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    message: str | None = None,
    current_user: User | None = Depends(get_current_user),
):
    if current_user and current_user.role in ADMIN_ROLES and current_user.is_active:
        return redirect("/admin")
    if current_user:
        request.session.clear()
    return templates.TemplateResponse(request, "login.html", {"request": request, "message": message})


@app.post("/admin/login")
async def login(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()], db: Session = Depends(get_db)):
    client_identifier = request_client_identifier(request)
    username = username.strip()
    trimmed_password = password.strip()
    password_candidates = (password,) if trimmed_password == password else (password, trimmed_password)
    try:
        attempt_id = begin_security_attempt(db, "admin_login", client_identifier, username)
    except SecurityLimitError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": str(exc), "username": username},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    valid_password_candidates = [candidate for candidate in password_candidates if len(candidate.encode("utf-8")) <= 72]
    if len(username) > 128 or not valid_password_candidates:
        finish_security_attempt(db, attempt_id, False, "invalid_credential_length")
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "账号或密码错误", "username": username[:128]},
            status_code=401,
        )
    user = None
    for password_candidate in valid_password_candidates:
        user = authenticate_user(db, username, password_candidate)
        if user:
            break
    if not user or user.role not in ADMIN_ROLES:
        finish_security_attempt(db, attempt_id, False, "invalid_credentials")
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "账号或密码错误",
                "username": username,
            },
            status_code=401,
        )
    user_id = user.id
    session_version = user.session_version
    finish_security_attempt(db, attempt_id, True, "authenticated")
    request.session["user_id"] = user_id
    request.session["session_version"] = session_version
    request.session[ADMIN_CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return redirect("/admin")


@app.post("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/admin/login")


@app.get("/admin/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    error: str | None = None,
    current_user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"request": request, "current_user": current_user, "error": error},
    )


@app.post("/admin/profile/password")
async def update_own_password(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    client_identifier = request_client_identifier(request)
    try:
        attempt_id = begin_security_attempt(db, "admin_login", client_identifier, current_user.username)
    except SecurityLimitError as exc:
        return templates.TemplateResponse(
            request,
            "profile.html",
            {"request": request, "current_user": current_user, "error": str(exc)},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    if len(current_password.encode("utf-8")) > 72 or not verify_password(current_password, current_user.password_hash):
        finish_security_attempt(db, attempt_id, False, "profile_password_mismatch")
        return redirect(message_url("/admin/profile", error="当前密码错误"))
    if new_password != confirm_password:
        finish_security_attempt(db, attempt_id, False, "profile_password_confirmation_mismatch")
        return redirect(message_url("/admin/profile", error="两次输入的新密码不一致"))
    if len(new_password) < 12 or len(new_password.encode("utf-8")) > 72:
        finish_security_attempt(db, attempt_id, False, "profile_password_invalid_length")
        return redirect(message_url("/admin/profile", error="新密码至少 12 位且不能超过 72 字节"))
    if verify_password(new_password, current_user.password_hash):
        finish_security_attempt(db, attempt_id, False, "profile_password_unchanged")
        return redirect(message_url("/admin/profile", error="新密码不能与当前密码相同"))

    current_user.password_hash = hash_password(new_password)
    current_user.session_version += 1
    current_user.updated_at = datetime.utcnow()
    add_audit(db, current_user.id, "change_own_password", "user", current_user.id)
    db.commit()
    finish_security_attempt(db, attempt_id, True, "profile_password_changed")
    request.session.clear()
    return redirect(message_url("/admin/login", message="密码已更新，请重新登录"))


def product_options(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).order_by(Product.status.asc(), Product.created_at.asc(), Product.id.asc())))


def scoped_inventory_groups(db: Session, current_user: User, product_id: int | None = None) -> dict[str, int]:
    return inventory_breakdown(db, product_id)


def product_stats(db: Session, product: Product) -> dict[str, int]:
    groups = inventory_breakdown(db, product.id)
    pending_cards = db.scalar(
        select(func.count()).select_from(Card).where(Card.product_id == product.id, Card.status == "pending")
    ) or 0
    sold_cards = db.scalar(
        select(func.count()).select_from(Card).where(Card.product_id == product.id, Card.status == "sold")
    ) or 0
    redemptions = db.scalar(
        select(func.count()).select_from(Redemption).where(Redemption.product_id == product.id)
    ) or 0
    return {
        **groups,
        "pending_cards": pending_cards,
        "sold_cards": sold_cards,
        "redemptions": redemptions,
        "health_used_24h": product_health_used_last_24h(db, product.id),
    }


def validate_product_form(
    name: str,
    sku: str,
    low_stock_threshold: int,
    health_timeout_seconds: int,
    health_daily_limit: int,
) -> tuple[str, str]:
    name = name.strip()
    sku = sku.strip().lower()
    if not name or len(name) > 120:
        raise ServiceError("商品名称不能为空且不能超过 120 位")
    if not sku or len(sku) > 80 or any(char in sku for char in "\\/\x00"):
        raise ServiceError("SKU 不能为空、不能超过 80 位且不能包含路径分隔符")
    if low_stock_threshold < 1 or low_stock_threshold > 9999:
        raise ServiceError("低库存阈值必须在 1 到 9999 之间")
    if health_timeout_seconds < 3 or health_timeout_seconds > 60:
        raise ServiceError("单次测活时间必须在 3 到 60 秒之间")
    if health_daily_limit < 0 or health_daily_limit > 100000:
        raise ServiceError("24 小时测活次数必须在 0 到 100000 之间；0 表示不限制")
    return name, sku


@app.get("/admin/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    products = product_options(db)
    stats_by_product = {product.id: product_stats(db, product) for product in products}
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "request": request,
            "current_user": current_user,
            "products": products,
            "stats_by_product": stats_by_product,
            "message": message,
            "error": error,
            "product_statuses": [PRODUCT_DRAFT, PRODUCT_LISTED, PRODUCT_HIDDEN],
        },
    )


@app.post("/admin/products")
async def create_product_route(
    name: Annotated[str, Form()],
    sku: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = PRODUCT_DRAFT,
    health_check_enabled: Annotated[str | None, Form()] = None,
    health_timeout_seconds: Annotated[int, Form()] = 15,
    health_daily_limit: Annotated[int, Form()] = 0,
    low_stock_threshold: Annotated[int, Form()] = 3,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        name, sku = validate_product_form(name, sku, low_stock_threshold, health_timeout_seconds, health_daily_limit)
        if status not in {PRODUCT_DRAFT, PRODUCT_LISTED, PRODUCT_HIDDEN}:
            raise ServiceError("商品状态无效")
        if db.scalar(select(Product.id).where(func.lower(Product.sku) == sku.lower())):
            raise ServiceError("SKU 已存在")
        timestamp = datetime.utcnow()
        product = Product(
            name=name,
            sku=sku,
            description=description.strip()[:1000],
            status=status,
            health_check_enabled=health_check_enabled == "on",
            health_timeout_seconds=health_timeout_seconds,
            health_daily_limit=health_daily_limit,
            low_stock_threshold=low_stock_threshold,
            creator_id=current_user.id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.add(product)
        db.flush()
        add_audit(db, current_user.id, "create_product", "product", product.id, product.sku)
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/products", error=str(exc)))
    return redirect(message_url("/admin/products", message=f"已创建商品 {product.name}"))


@app.post("/admin/products/{product_id}")
async def update_product_route(
    product_id: int,
    name: Annotated[str, Form()],
    sku: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = PRODUCT_DRAFT,
    health_check_enabled: Annotated[str | None, Form()] = None,
    health_timeout_seconds: Annotated[int, Form()] = 15,
    health_daily_limit: Annotated[int, Form()] = 0,
    low_stock_threshold: Annotated[int, Form()] = 3,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    product = db.get(Product, product_id)
    if not product:
        return redirect(message_url("/admin/products", error="商品不存在"))
    try:
        name, sku = validate_product_form(name, sku, low_stock_threshold, health_timeout_seconds, health_daily_limit)
        if status not in {PRODUCT_DRAFT, PRODUCT_LISTED, PRODUCT_HIDDEN}:
            raise ServiceError("商品状态无效")
        duplicate = db.scalar(select(Product.id).where(func.lower(Product.sku) == sku.lower(), Product.id != product.id))
        if duplicate:
            raise ServiceError("SKU 已存在")
        product.name = name
        product.sku = sku
        product.description = description.strip()[:1000]
        product.status = status
        product.health_check_enabled = health_check_enabled == "on"
        product.health_timeout_seconds = health_timeout_seconds
        product.health_daily_limit = health_daily_limit
        product.low_stock_threshold = low_stock_threshold
        product.updated_at = datetime.utcnow()
        add_audit(db, current_user.id, "update_product", "product", product.id, product.sku)
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/products", error=str(exc)))
    return redirect(message_url("/admin/products", message=f"已更新商品 {product.name}"))


def liveness_due_query(current_user: User, timestamp: datetime, product_id: int | None = None):
    stale_before = timestamp - LIVENESS_SYNC_INTERVAL
    query = select(ManagedFile).join(Product, ManagedFile.product_id == Product.id).where(
        ManagedFile.status == "available",
        Product.health_check_enabled.is_(True),
        or_(ManagedFile.account_checked_at.is_(None), ManagedFile.account_checked_at <= stale_before),
    )
    if product_id:
        query = query.where(ManagedFile.product_id == product_id)
    return scope_managed_files(query, current_user)


LIVENESS_REFRESH_MODES = {
    "due": "到期账号",
    "unchecked": "待测账号",
    "problem": "问题号",
    "all": "全部账号",
    "selected": "选中账号",
}


def liveness_refresh_query(current_user: User, timestamp: datetime, refresh_mode: str, selected_ids: set[int] | None = None, product_id: int | None = None):
    query = select(ManagedFile).join(Product, ManagedFile.product_id == Product.id).where(
        ManagedFile.status == "available",
        Product.health_check_enabled.is_(True),
    )
    if product_id:
        query = query.where(ManagedFile.product_id == product_id)
    if selected_ids:
        query = query.where(ManagedFile.id.in_(selected_ids))
    elif refresh_mode == "due":
        stale_before = timestamp - LIVENESS_SYNC_INTERVAL
        query = query.where(or_(ManagedFile.account_checked_at.is_(None), ManagedFile.account_checked_at <= stale_before))
    elif refresh_mode == "unchecked":
        query = query.where(
            or_(
                ManagedFile.account_status.is_(None),
                ManagedFile.account_status == "",
                ManagedFile.account_status == HEALTH_CHECKING,
                (ManagedFile.account_status == "available") & ManagedFile.account_checked_at.is_(None),
            )
        )
    elif refresh_mode == "problem":
        query = query.where(ManagedFile.account_status == "unavailable")
    elif refresh_mode == "all":
        pass
    else:
        stale_before = timestamp - LIVENESS_SYNC_INTERVAL
        query = query.where(or_(ManagedFile.account_checked_at.is_(None), ManagedFile.account_checked_at <= stale_before))
    return scope_managed_files(query, current_user)


@app.get("/admin/liveness", response_class=HTMLResponse)
def liveness_page(
    request: Request,
    product_id: int | None = None,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    timestamp = datetime.utcnow()
    selected_product = db.get(Product, product_id) if product_id else None
    selected_product_id = selected_product.id if selected_product else None
    groups = scoped_inventory_groups(db, current_user, selected_product_id)
    config_owner, _liveness_base_url, _liveness_management_key = liveness_pool_config_for_db(db)
    due_total = db.scalar(select(func.count()).select_from(liveness_due_query(current_user, timestamp, selected_product_id).subquery())) or 0
    recent_query = scope_managed_files(
        select(ManagedFile)
        .options(joinedload(ManagedFile.uploader), joinedload(ManagedFile.sold_card), joinedload(ManagedFile.product))
        .where(ManagedFile.status == "available"),
        current_user,
    )
    if selected_product_id:
        recent_query = recent_query.where(ManagedFile.product_id == selected_product_id)
    recent_files = list(
        db.scalars(
            recent_query.order_by(
                ManagedFile.account_checked_at.desc().nullslast(),
                ManagedFile.uploaded_at.desc(),
                ManagedFile.id.desc(),
            ).limit(80)
        )
    )
    return templates.TemplateResponse(
        request,
        "liveness.html",
        {
            "request": request,
            "current_user": current_user,
            "message": message,
            "error": error,
            "stats": {
                "活": groups["healthy"],
                "死": groups["problem"],
                "暂时未知": groups["unknown"],
                "待测文件": groups["unchecked"],
                "15分钟到期": due_total,
            },
            "recent_files": recent_files,
            "products": product_options(db),
            "selected_product_id": selected_product_id,
            "health_ready": health_client_ready(),
            "has_liveness_pool": True,
            "can_config_liveness": True,
            "liveness_pool_base_url": "",
            "liveness_has_secret": False,
            "liveness_last_sync_at": config_owner.liveness_last_sync_at if config_owner else None,
            "liveness_configured_at": config_owner.updated_at if config_owner else None,
            "refresh_modes": LIVENESS_REFRESH_MODES,
            "sub2_dashboard_url": "",
        },
    )


@app.post("/admin/liveness/pool")
async def update_liveness_pool_route(
    quota_pool_base_url: Annotated[str, Form()] = "",
    quota_pool_management_key: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    add_audit(db, current_user.id, "ignore_legacy_liveness_pool", "user", current_user.id)
    db.commit()
    return redirect(message_url("/admin/liveness", message="已切换为内置 Codex 独立测活，无需配置 SUB2"))


@app.post("/admin/liveness/upload-check")
async def upload_and_check_liveness_route(
    file: Annotated[list[UploadFile], File()],
    product_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    product = db.get(Product, product_id) if product_id else ensure_legacy_product(db, current_user.id)
    if not product:
        return redirect(message_url("/admin/liveness", error="商品不存在"))
    if not product.health_check_enabled:
        return redirect(message_url("/admin/liveness", error="该商品未启用测活"))
    upload_slot = False
    try:
        try:
            await asyncio.wait_for(UPLOAD_SEMAPHORE.acquire(), timeout=3)
            upload_slot = True
        except TimeoutError as exc:
            raise ServiceError("已有导入任务正在处理，请稍后重试") from exc
        if not file:
            raise ServiceError("请选择要上传测活的文件")
        buffered = await read_upload_batch(file, MAX_UPLOAD_BYTES, "单批上传文件总大小不能超过 500MB")
        budget = ImportBudget(max_accounts=50, max_documents=50)
        imported_files: list[ManagedFile] = []
        all_errors: list[str] = []
        for upload, raw in buffered:
            filename = Path(upload.filename or "").name or "未命名文件"
            try:
                items, errors = import_upload_files(db, current_user, upload.filename or "", raw, product, budget)
                imported_files.extend(items)
                all_errors.extend(errors)
            except ServiceError as exc:
                all_errors.append(f"{filename}: {exc}")
        unique_files = list({item.id: item for item in imported_files}.values())
        if not unique_files:
            raise ServiceError("没有导入可测活的账号文件")
        if len(unique_files) > 50:
            raise ServiceError("单次上传并测活最多 50 个账号，请分批处理")
        result = await run_liveness_checks_for_files(
            db,
            current_user,
            sorted(unique_files, key=lambda item: item.id),
            audit_action="upload_and_check_liveness",
        )
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/liveness", error=str(exc)))
    finally:
        if upload_slot:
            UPLOAD_SEMAPHORE.release()
    message = f"已导入 {len(unique_files)} 个账号，{result['message']}"
    if all_errors:
        message += f"，部分失败：{'；'.join(all_errors[:3])}"
    return redirect(message_url("/admin/liveness", message=message))


@app.post("/admin/liveness/check")
async def check_liveness_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return redirect(message_url("/admin/liveness", error="请选择要测活的账号文件"))
    if len(selected_ids) > 50:
        return redirect(message_url("/admin/liveness", error="单次最多检测 50 个文件"))
    files = list(
        db.scalars(
            scope_managed_files(select(ManagedFile).where(ManagedFile.id.in_(selected_ids)), current_user)
            .order_by(ManagedFile.id.asc())
        )
    )
    if len(files) != len(selected_ids):
        return redirect(message_url("/admin/liveness", error="部分文件不存在或无权限检测"))
    try:
        result = await run_liveness_checks_for_files(db, current_user, files, audit_action="manual_liveness_check")
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/liveness", error=str(exc)))
    return redirect(message_url("/admin/liveness", message=str(result["message"])))


@app.post("/admin/liveness/sync")
async def sync_liveness_route(
    product_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    timestamp = datetime.utcnow()
    config_owner, _base_url, _management_key = liveness_pool_config_for_db(db)
    selected_product_id = product_id if product_id and db.get(Product, product_id) else None
    files = list(
        db.scalars(
            liveness_due_query(current_user, timestamp, selected_product_id)
            .order_by(ManagedFile.account_checked_at.asc().nullsfirst(), ManagedFile.id.asc())
            .limit(50)
        )
    )
    if not files:
        if config_owner:
            config_owner.liveness_last_sync_at = timestamp
        db.commit()
        return redirect(message_url("/admin/liveness", message="没有到期需要同步的账号状态"))
    try:
        result = await run_liveness_checks_for_files(db, current_user, files, timestamp, "manual_liveness_sync")
        if config_owner:
            config_owner.liveness_last_sync_at = timestamp
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/liveness", error=str(exc)))
    return redirect(message_url("/admin/liveness", message=str(result["message"])))


@app.post("/admin/liveness/refresh")
async def refresh_liveness_route(
    ids: Annotated[list[int] | None, Form()] = None,
    refresh_mode: Annotated[str, Form()] = "due",
    product_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    timestamp = datetime.utcnow()
    config_owner, _base_url, _management_key = liveness_pool_config_for_db(db)
    selected_product_id = product_id if product_id and db.get(Product, product_id) else None
    selected_ids = set(ids or [])
    refresh_mode = "selected" if selected_ids else (refresh_mode or "due").strip()
    if refresh_mode not in LIVENESS_REFRESH_MODES:
        return redirect(message_url("/admin/liveness", error="刷新模式无效"))
    if selected_ids and len(selected_ids) > 50:
        return redirect(message_url("/admin/liveness", error="单次最多刷新 50 个账号"))
    files = list(
        db.scalars(
            liveness_refresh_query(current_user, timestamp, refresh_mode, selected_ids or None, selected_product_id)
            .order_by(ManagedFile.account_checked_at.asc().nullsfirst(), ManagedFile.id.asc())
            .limit(50)
        )
    )
    if selected_ids and len(files) != len(selected_ids):
        return redirect(message_url("/admin/liveness", error="部分文件不存在或无权限刷新"))
    if not files:
        if config_owner:
            config_owner.liveness_last_sync_at = timestamp
        db.commit()
        return redirect(message_url("/admin/liveness", message=f"{LIVENESS_REFRESH_MODES[refresh_mode]}没有可刷新额度的账号文件"))
    try:
        result = await run_liveness_checks_for_files(db, current_user, files, timestamp, "manual_liveness_refresh")
        if config_owner:
            config_owner.liveness_last_sync_at = timestamp
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/liveness", error=str(exc)))
    return redirect(message_url("/admin/liveness", message=f"手动刷新额度（{LIVENESS_REFRESH_MODES[refresh_mode]}）完成：{result['message']}"))


@app.post("/admin/liveness/delete-dead")
async def delete_dead_liveness_files_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    timestamp = datetime.utcnow()
    selected_ids = set(ids or [])
    query = select(ManagedFile).where(
        ManagedFile.status == "available",
        ManagedFile.account_status == "unavailable",
    )
    if selected_ids:
        query = query.where(ManagedFile.id.in_(selected_ids))
    files = list(
        db.scalars(
            scope_managed_files(query, current_user).order_by(ManagedFile.id.asc())
        )
    )
    if not files:
        empty_message = "选中的账号里没有可删除的死号" if selected_ids else "没有需要删除的死号"
        return redirect(message_url("/admin/liveness", message=empty_message))
    for item in files:
        apply_file_status(item, "voided", timestamp)
        add_audit(
            db,
            current_user.id,
            "delete_dead_liveness_file",
            "file",
            item.id,
            f"{item.original_name}:{item.account_error_label or item.account_error or 'dead'}",
        )
    db.commit()
    message = f"已批量删除 {len(files)} 个死号"
    return redirect(message_url("/admin/liveness", message=message))


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    def file_query():
        return scope_managed_files(select(ManagedFile), current_user)

    total_uploads = db.scalar(select(func.count()).select_from(file_query().subquery())) or 0
    today_uploads = db.scalar(select(func.count()).select_from(file_query().where(ManagedFile.uploaded_at >= today).subquery())) or 0
    yesterday_uploads = db.scalar(
        select(func.count()).select_from(file_query().where(ManagedFile.uploaded_at >= yesterday, ManagedFile.uploaded_at < today).subquery())
    ) or 0
    total_sold = db.scalar(select(func.count()).select_from(file_query().where(ManagedFile.status == "sold").subquery())) or 0
    today_sold = db.scalar(select(func.count()).select_from(file_query().where(ManagedFile.sold_at >= today).subquery())) or 0
    yesterday_sold = db.scalar(
        select(func.count()).select_from(file_query().where(ManagedFile.sold_at >= yesterday, ManagedFile.sold_at < today).subquery())
    ) or 0
    inventory_groups = scoped_inventory_groups(db, current_user)
    available_cards_query = scope_cards(select(Card).where(Card.status == "pending"), current_user)
    available_cards = db.scalar(select(func.count()).select_from(available_cards_query.subquery())) or 0
    today_redemptions_query = scope_redemptions(select(Redemption).where(Redemption.redeemed_at >= today), current_user)
    today_redemptions = db.scalar(select(func.count()).select_from(today_redemptions_query.subquery())) or 0
    active_links_query = select(TemporaryDownload).where(
        TemporaryDownload.revoked_at.is_(None),
        TemporaryDownload.expires_at > datetime.utcnow(),
    )
    active_links = db.scalar(select(func.count()).select_from(active_links_query.subquery())) or 0
    activity_query = select(AuditLog)
    recent_activity = list(db.scalars(activity_query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(12)))
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "stats": {
                "正常号分组": inventory_groups["normal"],
                "问题号分组": inventory_groups["problem"],
                "可用 CDK": available_cards,
                "今日兑换": today_redemptions,
                "有效临时链接": active_links,
                "累计上传": total_uploads,
                "累计交付": total_sold,
            },
            "comparison": {
                "今日上传": today_uploads,
                "昨日上传": yesterday_uploads,
                "今日交付": today_sold,
                "昨日交付": yesterday_sold,
            },
            "recent_activity": recent_activity,
        },
    )


@app.get("/admin/uploads", response_class=HTMLResponse)
def uploads_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        request,
        "uploads.html",
        {
            "request": request,
            "current_user": current_user,
            "message": message,
            "error": error,
            "products": product_options(db),
        },
    )


@app.post("/admin/uploads")
async def upload_file(
    file: Annotated[list[UploadFile], File()],
    product_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    product = db.get(Product, product_id) if product_id else ensure_legacy_product(db, current_user.id)
    if not product:
        return redirect(message_url("/admin/uploads", error="商品不存在"))
    upload_slot = False
    try:
        try:
            await asyncio.wait_for(UPLOAD_SEMAPHORE.acquire(), timeout=3)
            upload_slot = True
        except TimeoutError as exc:
            raise ServiceError("已有导入任务正在处理，请稍后重试") from exc
        total_imported = 0
        imported_file_ids: list[int] = []
        all_errors: list[str] = []
        if not file:
            raise ServiceError("请选择要上传的文件")
        buffered = await read_upload_batch(file, MAX_UPLOAD_BYTES, "单批上传文件总大小不能超过 500MB")
        budget = ImportBudget()
        for upload, raw in buffered:
            filename = Path(upload.filename or "").name or "未命名文件"
            try:
                items, errors = import_upload_files(db, current_user, upload.filename or "", raw, product, budget)
                total_imported += len(items)
                imported_file_ids.extend(item.id for item in items)
                all_errors.extend(errors)
            except ServiceError as exc:
                all_errors.append(f"{filename}: {exc}")
        if total_imported == 0 and all_errors:
            raise ServiceError("；".join(all_errors[:3]))
        db.commit()
        queued_for_liveness = schedule_uploaded_liveness(db, current_user.id, imported_file_ids)
    except TypeError:
        db.rollback()
        return redirect(message_url("/admin/uploads", error="上传处理失败"))
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/uploads", error=str(exc)))
    except Exception as exc:
        db.rollback()
        return redirect(message_url("/admin/uploads", error=f"上传失败：{exc}"))
    finally:
        if upload_slot:
            UPLOAD_SEMAPHORE.release()
    liveness_message = f"，已加入 {queued_for_liveness} 个账号的专属测活队列" if queued_for_liveness else ""
    if all_errors:
        return redirect(message_url("/admin/files", message=f"已导入 {total_imported} 个文件{liveness_message}，部分失败：{'；'.join(all_errors[:3])}"))
    return redirect(message_url("/admin/files", message=f"已导入 {total_imported} 个文件{liveness_message}"))


@app.post("/admin/uploads/manual")
async def upload_manual_json(
    payload: Annotated[str, Form()],
    filename: Annotated[str, Form()] = "manual-account.json",
    product_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    product = db.get(Product, product_id) if product_id else ensure_legacy_product(db, current_user.id)
    if not product:
        return redirect(message_url("/admin/uploads", error="商品不存在"))
    raw = payload.strip().encode("utf-8")
    if not raw:
        return redirect(message_url("/admin/uploads", error="请输入 JSON 内容"))
    if len(raw) > MAX_MANUAL_JSON_BYTES:
        return redirect(message_url("/admin/uploads", error="手动输入不能超过 2MB"))

    safe_name = Path(filename.strip() or "manual-account.json").name
    if len(safe_name) > 200:
        return redirect(message_url("/admin/uploads", error="文件名不能超过 200 位"))
    if Path(safe_name).suffix.lower() != ".json":
        safe_name = f"{safe_name}.json"
    try:
        items, errors = import_upload_files(db, current_user, safe_name, raw, product, ImportBudget())
        imported = len(items)
        if not items:
            raise ServiceError("没有识别到可入库账号")
        db.commit()
        queued_for_liveness = schedule_uploaded_liveness(db, current_user.id, [item.id for item in items])
    except ServiceError as exc:
        db.rollback()
        return redirect(message_url("/admin/uploads", error=str(exc)))
    except Exception as exc:
        db.rollback()
        return redirect(message_url("/admin/uploads", error=f"手动导入失败：{exc}"))

    message = f"已从手动输入导入 {imported} 个账号"
    if queued_for_liveness:
        message += f"，已加入 {queued_for_liveness} 个账号的专属测活队列"
    if errors:
        message += f"，部分失败：{'；'.join(errors[:3])}"
    return redirect(message_url("/admin/files", message=message))


@app.get("/admin/files", response_class=HTMLResponse)
def files_page(
    request: Request,
    q: str | None = None,
    card_code: str | None = None,
    product_id: int | None = None,
    status: str | None = None,
    account_status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int | None = 1,
    page_size: int | None = 50,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    page = normalize_page(page)
    page_size = normalize_page_size(page_size)
    query = scope_managed_files(
        select(ManagedFile).options(joinedload(ManagedFile.sold_card), joinedload(ManagedFile.uploader), joinedload(ManagedFile.product)),
        current_user,
    )
    if q:
        query = query.where(ManagedFile.original_name.contains(q.strip()))
    if product_id:
        query = query.where(ManagedFile.product_id == product_id)
    if card_code:
        card = db.scalar(scope_cards(select(Card).where(Card.code == card_code.strip().upper()), current_user))
        query = query.where(ManagedFile.sold_card_id == (card.id if card else -1))
    if status:
        query = query.where(ManagedFile.status == status)
    if account_status == "unchecked":
        query = query.where(
            or_(
                ManagedFile.account_status.is_(None),
                ManagedFile.account_status == "",
                ManagedFile.account_status == HEALTH_CHECKING,
                (ManagedFile.account_status == "available") & ManagedFile.account_checked_at.is_(None),
            )
        )
    elif account_status == "available":
        query = query.where(ManagedFile.account_status == "available", ManagedFile.account_checked_at.is_not(None))
    elif account_status:
        query = query.where(ManagedFile.account_status == account_status)
    start_dt = parse_date(start)
    end_dt = parse_date(end, end=True)
    if start_dt:
        query = query.where(ManagedFile.uploaded_at >= start_dt)
    if end_dt:
        query = query.where(ManagedFile.uploaded_at < end_dt)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = pagination_context(total, page, page_size)
    page = int(pagination["page"])
    files = list(db.scalars(query.order_by(ManagedFile.uploaded_at.desc(), ManagedFile.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return templates.TemplateResponse(
        request,
        "files.html",
        {
            "request": request,
            "current_user": current_user,
            "files": files,
            "message": message,
            "error": error,
            "filters": {
                "q": q or "",
                "card_code": card_code or "",
                "product_id": product_id or "",
                "status": status or "",
                "account_status": account_status or "",
                "start": start or "",
                "end": end or "",
            },
            "products": product_options(db),
            "pagination": pagination,
        },
    )


@app.post("/admin/files/void")
async def void_files_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    count = void_files(db, current_user, ids or [])
    db.commit()
    return redirect(message_url("/admin/files", message=f"已作废 {count} 个文件"))


@app.post("/admin/files/delete")
async def delete_files_route(
    request: Request,
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return action_result(request, "/admin/files", error="请选择要删除的账号", status_code=400)
    if len(selected_ids) > 100:
        return action_result(request, "/admin/files", error="单次最多删除 100 个账号", status_code=400)

    files = list(
        db.scalars(
            scope_managed_files(select(ManagedFile).where(ManagedFile.id.in_(selected_ids)), current_user)
            .order_by(ManagedFile.id.asc())
        )
    )
    if len(files) != len(selected_ids):
        return action_result(request, "/admin/files", error="部分账号不存在或无权限删除", status_code=403)

    redemption_filters = []
    for file_id in selected_ids:
        raw_id = str(file_id)
        redemption_filters.extend(
            [
                Redemption.file_ids == raw_id,
                Redemption.file_ids.like(f"{raw_id},%"),
                Redemption.file_ids.like(f"%,{raw_id},%"),
                Redemption.file_ids.like(f"%,{raw_id}"),
            ]
        )
    referenced_ids: set[int] = set()
    if redemption_filters:
        for raw_ids in db.scalars(select(Redemption.file_ids).where(or_(*redemption_filters))):
            referenced_ids.update(int(value) for value in raw_ids.split(",") if value.strip().isdigit())

    blocked = [
        item
        for item in files
        if item.status == "sold" or item.sold_card_id is not None or item.id in referenced_ids
    ]
    if blocked:
        return action_result(
            request,
            "/admin/files",
            error=f"有 {len(blocked)} 个账号已交付或被兑换记录引用，不能永久删除",
            status_code=409,
        )

    stored_paths: list[str] = []
    for item in files:
        stored_paths.append(item.stored_path)
        add_audit(
            db,
            current_user.id,
            "delete_inventory_file",
            "file",
            item.id,
            f"{item.original_name}:status={item.status}:account={item.account_status or 'unchecked'}",
        )
        db.delete(item)
    db.commit()

    upload_root = UPLOAD_DIR.resolve()
    local_delete_errors = 0
    for raw_path in stored_paths:
        try:
            path = Path(raw_path).resolve()
            if path.is_relative_to(upload_root) and path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            local_delete_errors += 1
            logger.warning("failed to remove deleted inventory file", exc_info=True)

    message = f"已永久删除 {len(files)} 个账号"
    if local_delete_errors:
        message += f"，本地文件清理异常 {local_delete_errors} 个"
    return action_result(request, "/admin/files", message=message)


@app.post("/admin/files/status")
async def update_files_status_route(
    request: Request,
    ids: Annotated[list[int] | None, Form()] = None,
    target_status: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    target_status = (target_status or "").strip()
    if not selected_ids:
        return action_result(request, "/admin/files", error="请选择要修改的文件", status_code=400)
    if target_status not in FILE_STATUSES:
        return action_result(request, "/admin/files", error="目标状态无效", status_code=400)
    query = scope_managed_files(select(ManagedFile).where(ManagedFile.id.in_(selected_ids)), current_user)
    files = list(db.scalars(query))
    if len(files) != len(selected_ids):
        return action_result(request, "/admin/files", error="部分文件不存在或无权限修改", status_code=403)
    timestamp = datetime.utcnow()
    for item in files:
        apply_file_status(item, target_status, timestamp)
        add_audit(db, current_user.id, "set_file_status", "file", item.id, f"{item.original_name}:{target_status}")
    db.commit()
    return action_result(request, "/admin/files", message=f"已修改 {len(files)} 个文件状态")


@app.post("/admin/files/account-status")
async def check_files_account_status_route(
    request: Request,
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return action_result(request, "/admin/files", error="请选择要检测的文件", status_code=400)
    if len(selected_ids) > 50:
        return action_result(request, "/admin/files", error="单次最多检测 50 个文件", status_code=400)

    query = scope_managed_files(select(ManagedFile).where(ManagedFile.id.in_(selected_ids)), current_user)
    files = list(db.scalars(query.order_by(ManagedFile.id.asc())))
    if len(files) != len(selected_ids):
        return action_result(request, "/admin/files", error="部分文件不存在或无权限检测", status_code=403)

    try:
        result = await run_liveness_checks_for_files(db, current_user, files)
    except ServiceError as exc:
        db.rollback()
        return action_result(request, "/admin/files", error=str(exc), status_code=400)
    db.commit()
    message = str(result["message"])
    if wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "message": message,
                "available": result["available"],
                "unavailable": result["unavailable"],
                "unknown": result.get("unknown", 0),
            }
        )
    return redirect(message_url("/admin/files", message=message))


@app.post("/admin/files/download")
async def download_files_route(
    ids: Annotated[list[int] | None, Form()] = None,
    mark_sold: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    if not ids:
        return redirect(message_url("/admin/files", error="请选择要下载的文件"))
    query = scope_managed_files(
        select(ManagedFile).where(ManagedFile.id.in_(ids), ManagedFile.status.in_(["available", "locked"])),
        current_user,
    )
    files = list(db.scalars(query.order_by(ManagedFile.id.asc())))
    if not files:
        return redirect(message_url("/admin/files", error="没有可下载的文件"))
    timestamp = datetime.utcnow()
    if len(files) == 1:
        item = files[0]
        item.latest_download_at = timestamp
        if mark_sold == "1" and item.status in {"available", "locked"}:
            item.status = "sold"
            item.sold_at = timestamp
        add_audit(db, current_user.id, "download_file", "file", item.id, item.original_name)
        db.commit()
        download_path = DOWNLOAD_DIR / f"file-{item.id}-{timestamp.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(6)}.json"
        download_path.write_bytes(read_account_file(item.stored_path))
        return FileResponse(download_path, media_type="application/json", filename=json_download_name(item.original_name))
    archive_path = DOWNLOAD_DIR / f"files_batch_{timestamp.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for item in files:
            arcname = json_download_name(item.original_name)
            if arcname in used_names:
                arcname = f"{item.id}_{arcname}"
            used_names.add(arcname)
            archive.writestr(arcname, read_account_file(item.stored_path))
    for item in files:
        item.latest_download_at = timestamp
        if mark_sold == "1" and item.status in {"available", "locked"}:
            item.status = "sold"
            item.sold_at = timestamp
        add_audit(db, current_user.id, "download_file", "file", item.id, item.original_name)
    db.commit()
    return FileResponse(archive_path, media_type="application/zip", filename=archive_path.name)


@app.get("/admin/cards", response_class=HTMLResponse)
def cards_page(
    request: Request,
    q: str | None = None,
    product_id: int | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int | None = 1,
    page_size: int | None = 50,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    page = normalize_page(page)
    page_size = normalize_page_size(page_size)
    query = scope_cards(select(Card).options(joinedload(Card.creator), joinedload(Card.product)), current_user)
    if q:
        query = query.where(Card.code.contains(q.strip().upper()))
    if product_id:
        query = query.where(Card.product_id == product_id)
    if status == "expired":
        query = query.where(Card.expires_at.is_not(None), Card.expires_at <= datetime.utcnow())
    elif status:
        query = query.where(Card.status == status)
    start_dt = parse_date(start)
    end_dt = parse_date(end, end=True)
    if start_dt:
        query = query.where(Card.used_at >= start_dt)
    if end_dt:
        query = query.where(Card.used_at < end_dt)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = pagination_context(total, page, page_size)
    page = int(pagination["page"])
    cards = list(db.scalars(query.order_by(Card.created_at.desc(), Card.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return templates.TemplateResponse(
        request,
        "cards.html",
        {
            "request": request,
            "current_user": current_user,
            "cards": cards,
            "message": message,
            "error": error,
            "filters": {"q": q or "", "product_id": product_id or "", "status": status or "", "start": start or "", "end": end or ""},
            "pagination": pagination,
            "current_time": datetime.utcnow(),
            "products": product_options(db),
        },
    )


@app.get("/admin/cards/{card_id}/redemptions")
def card_redemptions_route(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    query = scope_cards(select(Card).where(Card.id == card_id), current_user)
    card = db.scalar(query)
    if not card:
        raise HTTPException(status_code=404, detail="卡密不存在或无权限查看")

    redemptions = list(
        db.scalars(
            select(Redemption)
            .where(Redemption.card_id == card.id)
            .order_by(Redemption.redeemed_at.asc(), Redemption.id.asc())
        )
    )
    return {
        "card_code": card.code,
        "first_used_at": format_full_dt(card.used_at),
        "redemption_count": len(redemptions),
        "max_redemptions": card.max_redemptions,
        "expires_at": format_full_dt(card.expires_at, "永不过期"),
        "redemptions": [
            {
                "id": item.id,
                "redeemed_at": format_full_dt(item.redeemed_at),
                "output_format": redemption_format_label(item.output_format, item.download_path),
                "file_count": redemption_file_count(item.file_ids),
                "status": status_label(item.status),
                "can_regenerate": True,
            }
            for item in redemptions
        ],
    }


@app.post("/admin/cards/create")
async def create_cards_route(
    request: Request,
    product_id: Annotated[int | None, Form()] = None,
    file_count: Annotated[int, Form()] = 1,
    quantity: Annotated[int, Form()] = 1,
    max_redemptions: Annotated[int, Form()] = 1,
    expires_at: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        product = db.get(Product, product_id) if product_id else ensure_legacy_product(db, current_user.id)
        if not product:
            raise ServiceError("商品不存在")
        parsed_expiration = parse_card_expiration(expires_at)
        validate_card_policy(max_redemptions, parsed_expiration)
        cards = create_cards(db, current_user, file_count, quantity, product)
        for card in cards:
            card.max_redemptions = max_redemptions
            card.expires_at = parsed_expiration
        db.commit()
    except ServiceError as exc:
        db.rollback()
        if wants_json(request):
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return redirect(message_url("/admin/cards", error=str(exc)))
    if wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "count": len(cards),
                "codes": [card.code for card in cards],
                "filename": f"cdk_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt",
            },
            headers={"Cache-Control": "no-store"},
        )
    return redirect(message_url("/admin/cards", message=f"已生成 {len(cards)} 张卡密，单码最多使用 {max_redemptions} 次"))


@app.post("/admin/redemptions/{redemption_id}/link")
async def regenerate_redemption_link(
    request: Request,
    redemption_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    redemption = db.scalar(scope_redemptions(select(Redemption).where(Redemption.id == redemption_id), current_user))
    if not redemption:
        return JSONResponse({"ok": False, "error": "兑换记录不存在"}, status_code=404)
    try:
        cleanup_temporary_downloads(db)
        output_path, download_name, media_type = rebuild_redemption_download(db, redemption)
        token, link = create_temporary_download(
            db,
            output_path,
            download_name,
            media_type,
            "regenerate",
            redemption.id,
        )
        add_audit(db, current_user.id, "regenerate_download_link", "redemption", redemption.id, redemption.card.code)
        db.commit()
    except ServiceError as exc:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "download_url": public_download_url(token),
            "filename": download_name,
            "expires_at": f"{link.expires_at.isoformat()}Z",
            "expires_in": int(DOWNLOAD_TTL.total_seconds()),
        },
        headers=SENSITIVE_DOWNLOAD_HEADERS,
    )


@app.post("/admin/cards/void")
async def void_cards_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    count = void_cards(db, current_user, ids or [])
    db.commit()
    return redirect(message_url("/admin/cards", message=f"已作废 {count} 张卡密"))


@app.post("/admin/cards/policy")
async def update_card_policy_route(
    ids: Annotated[list[int] | None, Form()] = None,
    max_redemptions: Annotated[int, Form()] = 1,
    expires_at: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return redirect(message_url("/admin/cards", error="请选择要设置的卡密"))
    try:
        parsed_expiration = parse_card_expiration(expires_at)
        validate_card_policy(max_redemptions, parsed_expiration)
    except ServiceError as exc:
        return redirect(message_url("/admin/cards", error=str(exc)))
    cards = list(db.scalars(scope_cards(select(Card).where(Card.id.in_(selected_ids)), current_user)))
    if len(cards) != len(selected_ids):
        return redirect(message_url("/admin/cards", error="部分卡密不存在或无权限修改"))
    if any(card.redemption_count > max_redemptions for card in cards):
        return redirect(message_url("/admin/cards", error="使用次数不能低于卡密当前已兑换次数"))
    for card in cards:
        card.max_redemptions = max_redemptions
        card.expires_at = parsed_expiration
        if card.status != "voided" and card.redemption_count >= max_redemptions:
            card.status = "sold"
        elif card.status == "sold" and card.redemption_count < max_redemptions:
            card.status = "pending"
            card.voided_at = None
        add_audit(
            db,
            current_user.id,
            "set_card_policy",
            "card",
            card.id,
            f"max={max_redemptions};expires={parsed_expiration.isoformat() if parsed_expiration else 'never'}",
        )
    db.commit()
    return redirect(message_url("/admin/cards", message=f"已更新 {len(cards)} 张卡密的使用策略"))


@app.post("/admin/cards/extend")
async def extend_card_redemptions_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return redirect(message_url("/admin/cards", error="请选择要追加兑换次数的卡密"))
    cards = list(db.scalars(scope_cards(select(Card).where(Card.id.in_(selected_ids)), current_user)))
    if len(cards) != len(selected_ids):
        return redirect(message_url("/admin/cards", error="部分卡密不存在或无权限修改"))
    timestamp = datetime.utcnow()
    if any(card.status == "voided" for card in cards):
        return redirect(message_url("/admin/cards", error="已禁用卡密不能追加兑换次数"))
    if any(card.expires_at is not None and card.expires_at <= timestamp for card in cards):
        return redirect(message_url("/admin/cards", error="已过期卡密请先修改过期时间"))
    if any(card.max_redemptions >= 100 for card in cards):
        return redirect(message_url("/admin/cards", error="卡密最大使用次数不能超过 100"))
    for card in cards:
        card.max_redemptions += 1
        if card.status == "sold" and card.redemption_count < card.max_redemptions:
            card.status = "pending"
            card.voided_at = None
        add_audit(
            db,
            current_user.id,
            "extend_card_redemption",
            "card",
            card.id,
            f"{card.code}:max={card.max_redemptions}",
        )
    db.commit()
    return redirect(message_url("/admin/cards", message=f"已为 {len(cards)} 张卡密追加 1 次原文件兑换"))


@app.post("/admin/cards/disable")
async def disable_cards_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return redirect(message_url("/admin/cards", error="请选择要禁用的卡密"))
    cards = list(db.scalars(scope_cards(select(Card).where(Card.id.in_(selected_ids)), current_user)))
    if len(cards) != len(selected_ids):
        return redirect(message_url("/admin/cards", error="部分卡密不存在或无权限修改"))
    timestamp = datetime.utcnow()
    for card in cards:
        card.status = "voided"
        card.voided_at = timestamp
        add_audit(db, current_user.id, "disable_card", "card", card.id, card.code)
    db.commit()
    return redirect(message_url("/admin/cards", message=f"已禁用 {len(cards)} 张卡密"))


@app.post("/admin/cards/delete")
async def delete_cards_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    if not selected_ids:
        return redirect(message_url("/admin/cards", error="请选择要删除的卡密"))
    cards = list(db.scalars(scope_cards(select(Card).where(Card.id.in_(selected_ids)), current_user)))
    if len(cards) != len(selected_ids):
        return redirect(message_url("/admin/cards", error="部分卡密不存在或无权限删除"))
    redemptions = list(db.scalars(select(Redemption).where(Redemption.card_id.in_(selected_ids))))
    redemption_ids = [item.id for item in redemptions]
    download_links = (
        list(db.scalars(select(TemporaryDownload).where(TemporaryDownload.redemption_id.in_(redemption_ids))))
        if redemption_ids
        else []
    )
    bound_files = list(db.scalars(select(ManagedFile).where(ManagedFile.sold_card_id.in_(selected_ids))))
    for card in cards:
        add_audit(
            db,
            current_user.id,
            "delete_card",
            "card",
            card.id,
            f"{card.code}:status={card.status}:redemptions={card.redemption_count}",
        )
    for link in download_links:
        db.delete(link)
    for redemption in redemptions:
        db.delete(redemption)
    for item in bound_files:
        item.sold_card_id = None
    db.flush()
    for card in cards:
        db.delete(card)
    db.commit()
    return redirect(message_url("/admin/cards", message=f"已删除 {len(cards)} 张卡密及关联兑换记录"))


@app.post("/admin/cards/status")
async def update_cards_status_route(
    request: Request,
    ids: Annotated[list[int] | None, Form()] = None,
    target_status: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    selected_ids = set(ids or [])
    target_status = (target_status or "").strip()
    if not selected_ids:
        return action_result(request, "/admin/cards", error="请选择要修改的卡密", status_code=400)
    if target_status not in CARD_STATUSES:
        return action_result(request, "/admin/cards", error="目标状态无效", status_code=400)
    query = scope_cards(select(Card).where(Card.id.in_(selected_ids)), current_user)
    cards = list(db.scalars(query))
    if len(cards) != len(selected_ids):
        return action_result(request, "/admin/cards", error="部分卡密不存在或无权限修改", status_code=403)
    if target_status != "sold" and any(card.status == "sold" or card.used_at is not None for card in cards):
        return action_result(request, "/admin/cards", error="已兑换 CDK 不允许重新激活", status_code=400)
    timestamp = datetime.utcnow()
    for card in cards:
        apply_card_status(card, target_status, timestamp)
        add_audit(db, current_user.id, "set_card_status", "card", card.id, f"{card.code}:{target_status}")
    db.commit()
    return action_result(request, "/admin/cards", message=f"已修改 {len(cards)} 张卡密状态")


@app.post("/admin/cards/download")
async def download_cards_route(
    ids: Annotated[list[int] | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    if not ids:
        return redirect(message_url("/admin/cards", error="请选择要下载的卡密"))
    query = scope_cards(select(Card).where(Card.id.in_(ids), Card.status == "pending"), current_user)
    cards = list(db.scalars(query.order_by(Card.id.asc())))
    if not cards:
        return redirect(message_url("/admin/cards", error="没有可下载的卡密"))
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_path = DOWNLOAD_DIR / f"cards_batch_{timestamp}_{secrets.token_hex(8)}.txt"
    lines = []
    for card in cards:
        lines.append(card.code)
        add_audit(db, current_user.id, "download_card", "card", card.id, card.code)
    file_path.write_text("\n".join(lines), encoding="utf-8")
    db.commit()
    return FileResponse(file_path, media_type="text/plain; charset=utf-8", filename=file_path.name)


@app.get("/admin/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    users = list(db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())))
    return templates.TemplateResponse(
        request,
        "users.html",
        {"request": request, "current_user": current_user, "users": users, "message": message, "error": error},
    )


@app.post("/admin/users")
async def create_user_route(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()] = ROLE_ADMIN,
    quota_pool_base_url: Annotated[str, Form()] = "",
    quota_pool_management_key: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    username = username.strip()
    if not username or not password:
        return redirect(message_url("/admin/users", error="账号和密码不能为空"))
    if len(username) > 64:
        return redirect(message_url("/admin/users", error="账号长度不能超过 64 位"))
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        return redirect(message_url("/admin/users", error="密码至少 12 位且不能超过 72 字节"))
    if role != ROLE_ADMIN:
        return redirect(message_url("/admin/users", error="角色无效"))
    exists = db.scalar(select(User.id).where(User.username == username))
    if exists:
        return redirect(message_url("/admin/users", error="账号已存在"))
    timestamp = datetime.utcnow()
    created = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        quota_pool_base_url=quota_pool_base_url.strip(),
        quota_pool_management_key=quota_pool_management_key.strip(),
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(created)
    db.flush()
    add_audit(db, current_user.id, "create_admin", "user", created.id, created.username)
    db.commit()
    return redirect(message_url("/admin/users", message="账号已创建"))


@app.post("/admin/users/{user_id}/pool")
async def update_user_pool_route(
    user_id: int,
    quota_pool_base_url: Annotated[str, Form()] = "",
    quota_pool_management_key: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    target = db.scalar(select(User).where(User.id == user_id, User.role != ROLE_SUPER_ADMIN))
    if not target:
        return redirect(message_url("/admin/users", error="账号不存在"))
    target.quota_pool_base_url = quota_pool_base_url.strip()
    if quota_pool_management_key.strip():
        target.quota_pool_management_key = quota_pool_management_key.strip()
    target.updated_at = datetime.utcnow()
    add_audit(db, current_user.id, "update_admin_pool", "user", target.id, target.username)
    db.commit()
    return redirect(message_url("/admin/users", message=f"已更新账号 {target.username} 的号池配置"))


@app.post("/admin/users/{user_id}/toggle")
async def toggle_user_route(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    wants_json = "application/json" in request.headers.get("accept", "")
    target = db.scalar(select(User).where(User.id == user_id, User.role != ROLE_SUPER_ADMIN))
    if not target:
        if wants_json:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        return redirect(message_url("/admin/users", error="账号不存在"))
    if target.id == current_user.id:
        if wants_json:
            return JSONResponse({"ok": False, "error": "不能禁用当前登录账号"}, status_code=400)
        return redirect(message_url("/admin/users", error="不能禁用当前登录账号"))
    target.is_active = not target.is_active
    target.session_version += 1
    target.updated_at = datetime.utcnow()
    add_audit(db, current_user.id, "toggle_admin", "user", target.id, f"{target.username}:{target.is_active}")
    db.commit()
    state = "启用" if target.is_active else "禁用"
    if wants_json:
        return {
            "ok": True,
            "is_active": target.is_active,
            "label": state,
            "username": target.username,
            "updated_at": format_full_dt(target.updated_at),
        }
    return redirect(message_url("/admin/users", message=f"已{state}账号 {target.username}"))


@app.post("/admin/users/{user_id}/reset-password")
async def reset_user_password_route(
    user_id: int,
    password: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    target = db.scalar(select(User).where(User.id == user_id, User.role != ROLE_SUPER_ADMIN))
    if not target:
        return redirect(message_url("/admin/users", error="账号不存在"))
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        return redirect(message_url("/admin/users", error="新密码至少 12 位且不能超过 72 字节"))
    target.password_hash = hash_password(password)
    target.session_version += 1
    target.updated_at = datetime.utcnow()
    add_audit(db, current_user.id, "reset_admin_password", "user", target.id, target.username)
    db.commit()
    return redirect(message_url("/admin/users", message=f"已重置账号 {target.username} 的密码"))


@app.post("/admin/users/reset-password")
async def reset_user_password_form_route(
    user_id: Annotated[int, Form()],
    password: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    target = db.scalar(select(User).where(User.id == user_id, User.role != ROLE_SUPER_ADMIN))
    if not target:
        return redirect(message_url("/admin/users", error="账号不存在"))
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        return redirect(message_url("/admin/users", error="新密码至少 12 位且不能超过 72 字节"))
    target.password_hash = hash_password(password)
    target.session_version += 1
    target.updated_at = datetime.utcnow()
    add_audit(db, current_user.id, "reset_admin_password", "user", target.id, target.username)
    db.commit()
    return redirect(message_url("/admin/users", message=f"已重置账号 {target.username} 的密码"))
