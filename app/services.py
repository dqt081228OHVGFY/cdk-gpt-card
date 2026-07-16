import base64
import hashlib
import json
import re
import secrets
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from .database import DOWNLOAD_DIR, UPLOAD_DIR
from .models import ROLE_SUPER_ADMIN, AuditLog, Card, ManagedFile, Redemption, TemporaryDownload, User
from .security import generate_card_code


CARD_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CHINESE_NAME_PATTERN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
JSON_FILE_SUFFIXES = {".json", ".cpa", ".sub", ".sub2"}
MIN_CARD_FILE_COUNT = 1
MAX_JSON_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ACCOUNTS = 500
MAX_ARCHIVE_DOCUMENTS = 500
MAX_ARCHIVE_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200
MAX_REDEEM_FILES = 100
MAX_REDEEM_BYTES = 25 * 1024 * 1024
MAX_REDEMPTION_CANDIDATES = 500
FILES_PER_CARD = 1
SUB2API_DOWNLOAD_PREFIX = "sub2api"
DOWNLOAD_TTL = timedelta(hours=24)
CONVERT_DOWNLOAD_TTL = timedelta(minutes=10)
SECURE_RANDOM = secrets.SystemRandom()


class ServiceError(Exception):
    pass


class ResourceLimitError(ServiceError):
    pass


class FileClaimConflict(Exception):
    pass


@dataclass
class ImportBudget:
    max_accounts: int = MAX_IMPORT_ACCOUNTS
    max_documents: int = MAX_ARCHIVE_DOCUMENTS
    max_expanded_bytes: int = MAX_ARCHIVE_EXPANDED_BYTES
    accounts: int = 0
    documents: int = 0
    expanded_bytes: int = 0

    def consume_document(
        self,
        size: int,
        *,
        compressed_size: int | None = None,
        compression: int | None = None,
    ) -> None:
        if size < 0 or size > MAX_JSON_DOCUMENT_BYTES:
            raise ResourceLimitError("单个 JSON 文件不能超过 2MB")
        if self.documents + 1 > self.max_documents:
            raise ResourceLimitError(f"单批最多处理 {self.max_documents} 个账号文件")
        if self.expanded_bytes + size > self.max_expanded_bytes:
            raise ResourceLimitError("单批解压及 JSON 总大小不能超过 25MB")
        if compression is not None and compression not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ResourceLimitError("ZIP 仅支持 STORE 或 DEFLATE 压缩")
        if compressed_size is not None and size:
            ratio = size / max(compressed_size, 1)
            if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise ResourceLimitError("ZIP 压缩比异常，已拒绝处理")
        self.documents += 1
        self.expanded_bytes += size

    def consume_accounts(self, count: int) -> None:
        if count < 1:
            raise ServiceError("没有识别到可处理账号")
        if self.accounts + count > self.max_accounts:
            raise ResourceLimitError(f"单批最多处理 {self.max_accounts} 个账号")
        self.accounts += count


def now_utc() -> datetime:
    return datetime.utcnow()


def add_audit(db: Session, actor_id: int | None, action: str, target_type: str, target_id: int | None = None, detail: str | None = None) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


def validate_json_payload(raw: bytes) -> object:
    if len(raw) > MAX_JSON_DOCUMENT_BYTES:
        raise ResourceLimitError("单个 JSON 文件不能超过 2MB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ServiceError("JSON 必须使用 UTF-8 编码") from exc
    except json.JSONDecodeError as exc:
        raise ServiceError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
    except (RecursionError, ValueError) as exc:
        raise ServiceError("JSON 嵌套过深或数字超出允许范围") from exc
    if not isinstance(payload, (dict, list)):
        raise ServiceError("JSON 顶层必须是对象或数组；后续可按样例补充严格字段校验")
    return payload


def load_json_file(path: str | Path) -> object:
    try:
        with Path(path).open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except OSError as exc:
        raise ServiceError(f"JSON file cannot be read: {Path(path).name}") from exc
    except json.JSONDecodeError as exc:
        raise ServiceError(f"JSON format error: {Path(path).name} line {exc.lineno}, column {exc.colno}") from exc
    except (RecursionError, ValueError) as exc:
        raise ServiceError(f"JSON nesting or number is out of range: {Path(path).name}") from exc


def get_nested_value(data: dict, path: str) -> object | None:
    current: object = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def pick_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_token_value(data: dict, token_name: str) -> str:
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and isinstance(tokens.get(token_name), str):
        return tokens[token_name]
    if isinstance(data.get(token_name), str):
        return data[token_name]
    return ""


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise ServiceError("token 不是合法 JWT")

    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    normalized = (payload_segment + padding).replace("-", "+").replace("_", "/")
    try:
        payload_data = base64.b64decode(normalized)
        payload = json.loads(payload_data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError("无法解析 token payload") from exc
    if not isinstance(payload, dict):
        raise ServiceError("token payload must be an object")
    return payload


def detect_auth_format(data: dict) -> str:
    if data.get("auth_mode") == "chatgpt" and isinstance(data.get("tokens"), dict):
        return "chatgpt"
    if data.get("type") == "codex":
        return "codex"
    raise ServiceError("无法识别格式，需要 CPA/codex 或 ChatGPT auth JSON")


def extract_openai_auth_info(payload: dict) -> dict:
    auth_info = payload.get("https://api.openai.com/auth")
    return auth_info if isinstance(auth_info, dict) else {}


def resolve_tier(payload: dict) -> str:
    openai_auth = extract_openai_auth_info(payload)
    candidate = str(
        payload.get("tier")
        or payload.get("plan")
        or openai_auth.get("chatgpt_plan_type")
        or "unknown"
    ).strip().lower()
    if "team" in candidate:
        return "team"
    if "pro" in candidate:
        return "pro"
    if "plus" in candidate:
        return "plus"
    return candidate or "unknown"


def resolve_account_metadata(data: dict, access_payload: dict, id_payload: dict) -> dict[str, str]:
    fmt = detect_auth_format(data)
    access_auth_info = extract_openai_auth_info(access_payload)
    nested_account_id = get_nested_value(data, "tokens.account_id") if fmt == "chatgpt" else None
    account_id = pick_string(
        nested_account_id,
        data.get("account_id"),
        access_auth_info.get("chatgpt_account_id"),
        id_payload.get("sub"),
    )
    if not account_id:
        raise ServiceError("无法从 JSON 中解析 account_id")
    email = pick_string(data.get("email"), get_nested_value(data, "tokens.email"), id_payload.get("email")).lower()
    if not email:
        raise ServiceError("无法从 JSON 中解析 email")
    return {
        "account_id": account_id,
        "email": email,
        "tier": resolve_tier(id_payload or access_payload or {}),
    }


def normalize_auth_to_cpa(data: dict) -> dict:
    detect_auth_format(data)
    access_token = extract_token_value(data, "access_token")
    if not access_token:
        raise ServiceError("缺少 access_token，无法转换为 CPA")

    try:
        access_payload = decode_jwt_payload(access_token)
    except ServiceError:
        access_payload = {}
    id_token = extract_token_value(data, "id_token")
    try:
        id_payload = decode_jwt_payload(id_token) if id_token else {}
    except ServiceError:
        id_payload = {}
    metadata = resolve_account_metadata(data, access_payload, id_payload)
    return {
        "type": "codex",
        "account_id": metadata["account_id"],
        "email": metadata["email"],
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": extract_token_value(data, "refresh_token"),
        "last_refresh": extract_token_value(data, "last_refresh"),
    }


def normalize_raw_to_cpa(data: dict) -> dict:
    if detect_auth_format(data) != "chatgpt":
        raise ServiceError("输入不是 ChatGPT 原始 JSON")
    return normalize_auth_to_cpa(data)


def build_sub2api_account_entry(data: dict, source_path: str | Path) -> dict:
    normalized = normalize_auth_to_cpa(data)
    access_token = normalized["access_token"]

    access_payload = decode_jwt_payload(access_token)
    access_auth_info = extract_openai_auth_info(access_payload)
    id_token = normalized["id_token"]
    id_payload = decode_jwt_payload(id_token) if id_token else {}
    id_auth_info = extract_openai_auth_info(id_payload)
    organizations = id_auth_info.get("organizations")
    organization_id = organizations[0].get("id", "") if isinstance(organizations, list) and organizations and isinstance(organizations[0], dict) else ""

    metadata = resolve_account_metadata(normalized, access_payload, id_payload)
    exp = access_payload.get("exp")
    iat = access_payload.get("iat")
    expires_in = exp - iat if isinstance(exp, int) and isinstance(iat, int) and exp and iat else 864000
    email = metadata["email"]
    chatgpt_account_id = access_auth_info.get("chatgpt_account_id", metadata["account_id"])
    last_refresh = normalized["last_refresh"]
    client_id = access_payload.get("client_id") if isinstance(access_payload.get("client_id"), str) else ""

    return {
        "name": email.split("@", 1)[0] if email else Path(source_path).stem,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "chatgpt_account_id": chatgpt_account_id,
            "chatgpt_user_id": access_auth_info.get("chatgpt_user_id", ""),
            "client_id": client_id,
            "email": email,
            "expires_at": exp if isinstance(exp, int) else 0,
            "expires_in": expires_in,
            "id_token": id_token,
            "organization_id": organization_id,
            "plan_type": metadata["tier"],
            "refresh_token": normalized["refresh_token"],
        },
        "extra": {
            "email": email,
            "last_refresh": last_refresh,
        },
    }


def build_sub2api_dedupe_key(account: dict) -> str | None:
    credentials = account.get("credentials") if isinstance(account, dict) else {}
    extra = account.get("extra") if isinstance(account, dict) else {}
    if not isinstance(credentials, dict):
        credentials = {}
    if not isinstance(extra, dict):
        extra = {}

    chatgpt_user_id = credentials.get("chatgpt_user_id") or extra.get("chatgpt_user_id") or ""
    chatgpt_account_id = credentials.get("chatgpt_account_id") or extra.get("chatgpt_account_id") or ""
    if isinstance(chatgpt_user_id, str) and chatgpt_user_id and isinstance(chatgpt_account_id, str) and chatgpt_account_id:
        return f"account-user:{chatgpt_user_id}|{chatgpt_account_id}"

    refresh_token = credentials.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        return f"refresh:{refresh_token}"

    access_token = credentials.get("access_token")
    if isinstance(access_token, str) and access_token:
        access_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
        return f"access:{access_hash}"

    email = credentials.get("email") or extra.get("email")
    if isinstance(email, str) and email:
        organization_id = credentials.get("organization_id") or extra.get("organization_id") or ""
        plan_type = credentials.get("plan_type") or extra.get("plan_type") or ""
        return f"account:{email}|{chatgpt_account_id}|{organization_id}|{plan_type}"
    return None


def apply_sub2api_defaults(account_entry: dict) -> dict:
    account_entry["concurrency"] = 10
    account_entry["priority"] = 1
    account_entry["rate_multiplier"] = 1
    account_entry["auto_pause_on_expired"] = True
    return account_entry


def build_sub2api_config_from_payloads(payloads: list[tuple[dict, str]]) -> dict:
    accounts: list[dict] = []
    seen_keys: set[str] = set()
    for data, source_name in payloads:
        account = apply_sub2api_defaults(build_sub2api_account_entry(data, source_name))
        dedupe_key = build_sub2api_dedupe_key(account)
        if dedupe_key and dedupe_key in seen_keys:
            continue
        if dedupe_key:
            seen_keys.add(dedupe_key)
        accounts.append(account)
    if not accounts:
        raise ServiceError("没有成功转换任何账号")
    return {"accounts": accounts, "proxies": []}


def build_sub2api_config(files: list["ManagedFile"]) -> dict:
    payloads: list[tuple[dict, str]] = []
    for item in files:
        data = load_json_file(item.stored_path)
        if not isinstance(data, dict):
            raise ServiceError(f"JSON root must be an object: {item.original_name}")
        payloads.append((data, item.original_name))
    return build_sub2api_config_from_payloads(payloads)


def sub2api_account_to_cpa(account: dict) -> dict:
    credentials = account.get("credentials")
    extra = account.get("extra")
    if not isinstance(credentials, dict):
        raise ServiceError("SUB 账号缺少 credentials")
    if not isinstance(extra, dict):
        extra = {}

    access_token = pick_string(credentials.get("access_token"))
    if not access_token:
        raise ServiceError("SUB 账号缺少 access_token")
    try:
        access_payload = decode_jwt_payload(access_token)
    except ServiceError:
        access_payload = {}
    access_auth = extract_openai_auth_info(access_payload)
    email = pick_string(credentials.get("email"), extra.get("email")).lower()
    account_id = pick_string(credentials.get("chatgpt_account_id"), extra.get("chatgpt_account_id"), access_auth.get("chatgpt_account_id"))
    if not email:
        raise ServiceError("SUB 账号缺少 email")
    if not account_id:
        raise ServiceError(f"无法从 SUB 账号解析 account_id：{email}")
    return {
        "type": "codex",
        "account_id": account_id,
        "email": email,
        "access_token": access_token,
        "id_token": pick_string(credentials.get("id_token")),
        "refresh_token": pick_string(credentials.get("refresh_token")),
        "last_refresh": pick_string(extra.get("last_refresh")),
    }


def sub2api_config_to_cpa(payload: dict) -> list[dict]:
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ServiceError("SUB JSON 中没有 accounts")
    if len(accounts) > MAX_IMPORT_ACCOUNTS:
        raise ResourceLimitError(f"单批最多处理 {MAX_IMPORT_ACCOUNTS} 个账号")
    converted: list[dict] = []
    errors: list[str] = []
    for index, account in enumerate(accounts, start=1):
        if not isinstance(account, dict):
            errors.append(f"第 {index} 个账号不是对象")
            continue
        try:
            converted.append(sub2api_account_to_cpa(account))
        except ServiceError as exc:
            errors.append(str(exc))
    if not converted:
        raise ServiceError("；".join(errors) or "没有可转换的 SUB 账号")
    return converted


def account_email_from_cpa(payload: dict) -> str:
    email = pick_string(payload.get("email")).lower()
    if email:
        return email
    id_token = extract_token_value(payload, "id_token")
    if id_token:
        try:
            return pick_string(decode_jwt_payload(id_token).get("email")).lower()
        except ServiceError:
            pass
    return ""


def cpa_output_name(payload: dict, fallback: str, index: int = 1) -> str:
    email = account_email_from_cpa(payload)
    if email:
        safe_email = re.sub(r"[^A-Za-z0-9@._+-]", "_", email)
        return f"{safe_email}.json"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(fallback).stem).strip("._") or f"account-{index}"
    return f"{stem}.json"


def classify_json_payload(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("accounts"), list):
        return "sub"
    if isinstance(payload, dict):
        try:
            auth_format = detect_auth_format(payload)
            return "raw" if auth_format == "chatgpt" else "cpa"
        except ServiceError:
            return "json"
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            formats = {classify_json_payload(item) for item in payload}
            if formats == {"raw"}:
                return "raw_batch"
            if formats == {"cpa"}:
                return "cpa_batch"
            if formats <= {"raw", "cpa"}:
                return "auth_batch"
            return "json_batch"
        raise ServiceError("JSON 数组必须由 CPA 账号对象组成")
    raise ServiceError("无法识别 JSON 格式")


def expand_to_cpa_payloads(payload: object) -> tuple[list[dict], str]:
    account_batch = payload.get("accounts") if isinstance(payload, dict) else payload
    if isinstance(account_batch, list) and len(account_batch) > MAX_IMPORT_ACCOUNTS:
        raise ResourceLimitError(f"单批最多处理 {MAX_IMPORT_ACCOUNTS} 个账号")
    source_format = classify_json_payload(payload)
    if source_format == "sub":
        return sub2api_config_to_cpa(payload), source_format
    if source_format in {"raw_batch", "cpa_batch", "auth_batch"}:
        return [normalize_auth_to_cpa(item) for item in payload], source_format
    if source_format == "raw":
        return [normalize_raw_to_cpa(payload)], source_format
    if source_format == "cpa":
        return [normalize_auth_to_cpa(payload)], source_format
    if source_format == "json_batch":
        return list(payload), source_format
    return [payload], source_format


def json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def display_json_name(filename: str) -> str:
    path = Path(filename).name
    return Path(path).stem if Path(path).suffix.lower() == ".json" else path


def json_download_name(filename: str) -> str:
    path = Path(filename).name
    return path if Path(path).suffix.lower() == ".json" else f"{path}.json"


def validate_upload_filename(filename: str) -> None:
    display_name = Path(filename).name
    if CHINESE_NAME_PATTERN.search(display_name):
        raise ServiceError(f"文件名不能包含中文：{display_name}")


def save_json_file(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    batch_name: str | None = None,
    source_format: str = "cpa",
    account_email: str | None = None,
) -> ManagedFile:
    validate_upload_filename(filename)
    validate_json_payload(raw)
    original_name = display_json_name(filename)
    timestamp = now_utc()
    identity_filters = [ManagedFile.original_name == original_name]
    if account_email:
        identity_filters.append(func.lower(ManagedFile.account_email) == account_email.lower())
    existing = db.scalar(
        select(ManagedFile)
        .where(ManagedFile.uploader_id == uploader.id, or_(*identity_filters))
        .order_by(ManagedFile.id.desc())
    )
    if existing:
        target = Path(existing.stored_path)
        target.write_bytes(raw)
        existing.original_name = original_name
        existing.uploaded_at = timestamp
        existing.batch_name = batch_name
        existing.source_format = source_format
        existing.account_email = account_email
        existing.account_status = None
        existing.account_checked_at = None
        existing.account_error = ""
        existing.account_error_label = ""
        add_audit(db, uploader.id, "replace_file", "file", existing.id, existing.original_name)
        return existing

    stored_name = f"{uuid.uuid4().hex}_{original_name}"
    target = UPLOAD_DIR / stored_name
    target.write_bytes(raw)
    managed = ManagedFile(
        original_name=original_name,
        stored_path=str(target),
        generated_at=timestamp,
        uploader_id=uploader.id,
        batch_name=batch_name,
        source_format=source_format,
        account_email=account_email,
        account_status=None,
        account_error="",
        account_error_label="",
    )
    db.add(managed)
    db.flush()
    add_audit(db, uploader.id, "upload_file", "file", managed.id, managed.original_name)
    return managed


def import_json_payload_files(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    batch_name: str | None = None,
    budget: ImportBudget | None = None,
    document_precounted: bool = False,
) -> list[ManagedFile]:
    budget = budget or ImportBudget()
    if not document_precounted:
        budget.consume_document(len(raw))
    payload = validate_json_payload(raw)
    accounts, source_format = expand_to_cpa_payloads(payload)
    budget.consume_accounts(len(accounts))
    return save_prepared_json_payload_files(db, uploader, filename, raw, accounts, source_format, batch_name)


def save_prepared_json_payload_files(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    accounts: list[dict],
    source_format: str,
    batch_name: str | None = None,
) -> list[ManagedFile]:
    imported_files: list[ManagedFile] = []
    for index, account in enumerate(accounts, start=1):
        output_name = Path(filename).name if len(accounts) == 1 and source_format == "cpa" else cpa_output_name(account, filename, index)
        if len(accounts) == 1 and source_format in {"cpa", "json"}:
            output_name = Path(filename).name
            output_raw = raw
        else:
            output_raw = json_bytes(account)
        imported_files.append(save_json_file(
            db,
            uploader,
            output_name,
            output_raw,
            batch_name=batch_name,
            source_format=(
                "sub"
                if source_format == "sub"
                else "raw"
                if source_format.startswith("raw")
                else "cpa"
                if source_format.startswith("cpa")
                else "mixed"
                if source_format == "auth_batch"
                else "unknown"
            ),
            account_email=account_email_from_cpa(account) or None,
        ))
    return imported_files


def import_json_payload(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    batch_name: str | None = None,
) -> int:
    return len(import_json_payload_files(db, uploader, filename, raw, batch_name=batch_name))


def import_upload(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    budget: ImportBudget | None = None,
) -> tuple[int, list[str]]:
    imported_files, errors = import_upload_files(db, uploader, filename, raw, budget)
    return len(imported_files), errors


def import_upload_files(
    db: Session,
    uploader: User,
    filename: str,
    raw: bytes,
    budget: ImportBudget | None = None,
) -> tuple[list[ManagedFile], list[str]]:
    budget = budget or ImportBudget()
    validate_upload_filename(filename)
    suffix = Path(filename).suffix.lower()
    errors: list[str] = []
    imported_files: list[ManagedFile] = []
    if suffix in JSON_FILE_SUFFIXES:
        json_name = f"{Path(filename).stem}.json"
        imported_files.extend(import_json_payload_files(db, uploader, json_name, raw, budget=budget))
    elif suffix == ".zip":
        batch_name = Path(filename).name
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if not infos:
                    raise ServiceError("ZIP 不能为空")
                if len(infos) > budget.max_documents:
                    raise ResourceLimitError(f"ZIP 内文件数量不能超过 {budget.max_documents}")
                documents: list[tuple[str, bytes]] = []
                for info in infos:
                    inner_name = Path(info.filename).name
                    if Path(inner_name).suffix.lower() not in JSON_FILE_SUFFIXES:
                        errors.append(f"{inner_name}: ZIP 内只允许 JSON/CPA/SUB 文件")
                        continue
                    if info.flag_bits & 0x1:
                        raise ServiceError("不支持加密 ZIP")
                    budget.consume_document(
                        info.file_size,
                        compressed_size=info.compress_size,
                        compression=info.compress_type,
                    )
                    documents.append((f"{Path(inner_name).stem}.json", archive.read(info)))

                prepared: list[tuple[str, bytes, list[dict], str]] = []
                for json_name, document_raw in documents:
                    try:
                        payload = validate_json_payload(document_raw)
                        accounts, source_format = expand_to_cpa_payloads(payload)
                        budget.consume_accounts(len(accounts))
                        prepared.append((json_name, document_raw, accounts, source_format))
                    except ResourceLimitError:
                        raise
                    except ServiceError as exc:
                        errors.append(f"{json_name}: {exc}")

                for json_name, document_raw, accounts, source_format in prepared:
                    imported_files.extend(
                        save_prepared_json_payload_files(
                            db,
                            uploader,
                            json_name,
                            document_raw,
                            accounts,
                            source_format,
                            batch_name,
                        )
                    )
        except zipfile.BadZipFile as exc:
            raise ServiceError("ZIP 文件无法读取") from exc
    else:
        raise ServiceError("只支持上传 .json、.cpa、.sub、.sub2 或 .zip 文件")
    if not imported_files and errors:
        raise ServiceError("；".join(errors))
    return imported_files, errors


def collect_json_documents(uploads: list[tuple[str, bytes]]) -> list[tuple[str, object]]:
    documents: list[tuple[str, object]] = []
    budget = ImportBudget()
    for filename, raw in uploads:
        suffix = Path(filename).suffix.lower()
        if suffix in JSON_FILE_SUFFIXES:
            budget.consume_document(len(raw))
            documents.append((Path(filename).name, validate_json_payload(raw)))
            continue
        if suffix != ".zip":
            raise ServiceError(f"只支持 JSON/CPA/SUB 或 ZIP：{Path(filename).name}")
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if not infos:
                    raise ServiceError("ZIP 不能为空")
                if len(infos) > budget.max_documents:
                    raise ResourceLimitError(f"ZIP 内文件数量不能超过 {budget.max_documents}")
                for info in infos:
                    inner_name = Path(info.filename).name
                    if Path(inner_name).suffix.lower() not in JSON_FILE_SUFFIXES:
                        raise ServiceError(f"ZIP 内只允许 JSON/CPA/SUB：{inner_name}")
                    if info.flag_bits & 0x1:
                        raise ServiceError("不支持加密 ZIP")
                    budget.consume_document(
                        info.file_size,
                        compressed_size=info.compress_size,
                        compression=info.compress_type,
                    )
                    documents.append((inner_name, validate_json_payload(archive.read(info))))
        except zipfile.BadZipFile as exc:
            raise ServiceError("ZIP 文件无法读取") from exc
    if not documents:
        raise ServiceError("没有找到可转换的 JSON")
    return documents


def convert_json_uploads(
    uploads: list[tuple[str, bytes]],
    target_format: str,
    timestamp: datetime | None = None,
) -> tuple[Path, str, str, str, int]:
    if target_format not in {"cpa", "sub"}:
        raise ServiceError("目标格式必须是 CPA 或 SUB")
    timestamp = timestamp or now_utc()
    documents = collect_json_documents(uploads)
    cpa_payloads: list[tuple[dict, str]] = []
    source_formats: set[str] = set()
    for source_name, payload in documents:
        accounts, source_format = expand_to_cpa_payloads(payload)
        if len(cpa_payloads) + len(accounts) > MAX_IMPORT_ACCOUNTS:
            raise ResourceLimitError(f"单次转换最多处理 {MAX_IMPORT_ACCOUNTS} 个账号")
        if source_format in {"json", "json_batch"}:
            raise ServiceError(f"无法识别账号格式：{source_name}")
        if source_format == "sub":
            source_formats.add("sub")
        elif source_format.startswith("raw"):
            source_formats.add("json")
        elif source_format == "auth_batch":
            source_formats.update({"cpa", "json"})
        else:
            source_formats.add("cpa")
        cpa_payloads.extend((account, source_name) for account in accounts)

    source_label = "+".join(sorted(source_formats)).upper()
    stamp = timestamp.strftime("%Y%m%d%H%M%S")
    unique = uuid.uuid4().hex
    if target_format == "sub":
        output = build_sub2api_config_from_payloads(cpa_payloads)
        download_name = f"sub2api-{stamp}-converted.json"
        output_path = DOWNLOAD_DIR / f"conversion-sub-{stamp}-{unique}.json"
        output_path.write_bytes(json_bytes(output))
        return output_path, download_name, "application/json", source_label, len(output["accounts"])

    output_accounts = [payload for payload, _ in cpa_payloads]
    if len(output_accounts) == 1:
        download_name = cpa_output_name(output_accounts[0], cpa_payloads[0][1])
        output_path = DOWNLOAD_DIR / f"conversion-cpa-{stamp}-{unique}.json"
        output_path.write_bytes(json_bytes(output_accounts[0]))
        return output_path, download_name, "application/json", source_label, 1

    download_name = f"cpa-{stamp}-{len(output_accounts)}-accounts.json"
    output_path = DOWNLOAD_DIR / f"conversion-cpa-{stamp}-{unique}.json"
    output_path.write_bytes(json_bytes(output_accounts))
    return output_path, download_name, "application/json", source_label, len(output_accounts)


def create_temporary_download(
    db: Session,
    file_path: str | Path,
    download_name: str,
    media_type: str,
    purpose: str,
    redemption_id: int | None = None,
    timestamp: datetime | None = None,
) -> tuple[str, TemporaryDownload]:
    timestamp = timestamp or now_utc()
    resolved_path = Path(file_path).resolve()
    if not resolved_path.is_file() or resolved_path.parent != DOWNLOAD_DIR.resolve():
        raise ServiceError("临时下载文件路径无效")
    token = secrets.token_urlsafe(32)
    ttl = CONVERT_DOWNLOAD_TTL if purpose == "convert" else DOWNLOAD_TTL
    link = TemporaryDownload(
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        redemption_id=redemption_id,
        file_path=str(resolved_path),
        download_name=Path(download_name).name,
        media_type=media_type,
        purpose=purpose,
        created_at=timestamp,
        expires_at=timestamp + ttl,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return token, link


def resolve_temporary_download(db: Session, token: str, timestamp: datetime | None = None) -> TemporaryDownload:
    timestamp = timestamp or now_utc()
    if len(token) < 32 or len(token) > 128:
        raise ServiceError("下载链接已失效，请重新生成")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    link = db.scalar(select(TemporaryDownload).where(TemporaryDownload.token_hash == token_hash))
    if not link or link.revoked_at or link.expires_at <= timestamp:
        raise ServiceError("下载链接已失效，请重新生成")
    resolved_path = Path(link.file_path).resolve()
    if resolved_path.parent != DOWNLOAD_DIR.resolve() or not resolved_path.is_file():
        raise ServiceError("临时文件已清理，请重新生成下载链接")
    link.download_count += 1
    link.last_download_at = timestamp
    link.revoked_at = timestamp
    db.commit()
    return link


def cleanup_temporary_downloads(db: Session, timestamp: datetime | None = None) -> int:
    timestamp = timestamp or now_utc()
    expired = list(
        db.scalars(
            select(TemporaryDownload).where(
                TemporaryDownload.revoked_at.is_(None),
                TemporaryDownload.expires_at <= timestamp,
            )
        )
    )
    expired_paths = {item.file_path for item in expired}
    for item in expired:
        item.revoked_at = timestamp

    active_paths = set(
        db.scalars(
            select(TemporaryDownload.file_path).where(
                TemporaryDownload.revoked_at.is_(None),
                TemporaryDownload.expires_at > timestamp,
            )
        )
    )
    removed = 0
    for raw_path in expired_paths:
        if raw_path in active_paths:
            continue
        path = Path(raw_path)
        if path.is_file() and path.parent.resolve() == DOWNLOAD_DIR.resolve():
            path.unlink(missing_ok=True)
            removed += 1

    cutoff = timestamp - DOWNLOAD_TTL
    for path in DOWNLOAD_DIR.iterdir():
        if not path.is_file() or str(path) in active_paths:
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if modified <= cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    db.commit()
    return removed


def create_cards(db: Session, creator: User, file_count: int, quantity: int = 1) -> list[Card]:
    if file_count != FILES_PER_CARD:
        raise ServiceError("一户一码固定绑定 1 个 JSON 文件")
    if quantity < 1 or quantity > 200:
        raise ServiceError("单次生成数量必须在 1 到 200 之间")
    cards = []
    for _ in range(quantity):
        card = Card(code=generate_card_code(db), creator_id=creator.id, file_count=file_count, status="pending")
        db.add(card)
        db.flush()
        add_audit(db, creator.id, "create_card", "card", card.id, f"{card.code}:{file_count}")
        cards.append(card)
    return cards


def normal_inventory_filter():
    return (
        ManagedFile.status == "available",
        ManagedFile.account_status == "available",
        ManagedFile.account_checked_at.is_not(None),
    )


def inventory_breakdown(db: Session, uploader_id: int | None = None) -> dict[str, int]:
    owner_filter = (ManagedFile.uploader_id == uploader_id,) if uploader_id is not None else ()
    normal = db.scalar(select(func.count()).select_from(ManagedFile).where(*normal_inventory_filter(), *owner_filter)) or 0
    healthy = db.scalar(
        select(func.count()).select_from(ManagedFile).where(
            ManagedFile.status == "available",
            ManagedFile.account_status == "available",
            ManagedFile.account_checked_at.is_not(None),
            *owner_filter,
        )
    ) or 0
    problem = db.scalar(
        select(func.count()).select_from(ManagedFile).where(
            ManagedFile.status == "available",
            ManagedFile.account_status == "unavailable",
            *owner_filter,
        )
    ) or 0
    unchecked = db.scalar(
        select(func.count()).select_from(ManagedFile).where(
            ManagedFile.status == "available",
            or_(
                ManagedFile.account_status.is_(None),
                ManagedFile.account_status == "",
                (ManagedFile.account_status == "available") & ManagedFile.account_checked_at.is_(None),
            ),
            *owner_filter,
        )
    ) or 0
    return {"normal": normal, "healthy": healthy, "problem": problem, "unchecked": unchecked, "total": normal + problem}


def inventory_count(db: Session) -> int:
    return inventory_breakdown(db)["normal"]


def weighted_pick(files: list[ManagedFile], count: int) -> list[ManagedFile]:
    pool = sorted(files, key=lambda item: (item.uploaded_at, item.id))
    picked: list[ManagedFile] = []
    while len(picked) < count:
        weights = list(range(len(pool), 0, -1))
        chosen = SECURE_RANDOM.choices(pool, weights=weights, k=1)[0]
        picked.append(chosen)
        pool.remove(chosen)
    return picked


def parse_card_codes(raw_codes: str) -> list[str]:
    if len(raw_codes) > MAX_REDEEM_FILES * 40:
        raise ResourceLimitError(f"单次最多支持 {MAX_REDEEM_FILES} 个 CDK")
    codes = [item.strip().lower() for item in re.split(r"[\s,，;；]+", raw_codes) if item.strip()]
    if not codes:
        raise ServiceError("请输入卡密")
    if len(codes) > MAX_REDEEM_FILES:
        raise ResourceLimitError(f"单次最多支持 {MAX_REDEEM_FILES} 个 CDK")
    if len(codes) != len(set(codes)):
        raise ServiceError("卡密不能重复输入")
    for code in codes:
        if not CARD_PATTERN.match(code):
            raise ServiceError("卡密格式错误，需要 32 位十六进制字符串")
    return codes


def assert_card_can_redeem(card: Card | None, code: str, timestamp: datetime) -> Card:
    if not card:
        raise ServiceError(f"卡密不存在：{code}")
    if card.status == "sold":
        raise ServiceError(f"卡密已使用：{code}")
    if card.status == "voided":
        raise ServiceError(f"卡密已作废：{code}")
    if card.status != "pending":
        raise ServiceError(f"卡密状态不可使用：{code}")
    if card.expires_at is not None and card.expires_at <= timestamp:
        raise ServiceError(f"卡密已过期：{code}")
    if card.max_redemptions < 1 or card.redemption_count >= card.max_redemptions:
        raise ServiceError(f"卡密使用次数已达上限：{code}")
    return card


def claim_available_files(db: Session, file_ids: list[int]) -> None:
    if not file_ids:
        return
    result = db.execute(
        update(ManagedFile)
        .where(ManagedFile.id.in_(file_ids), ManagedFile.status == "available")
        .where(ManagedFile.account_status == "available", ManagedFile.account_checked_at.is_not(None))
        .values(status="locked")
    )
    if result.rowcount != len(file_ids):
        raise FileClaimConflict


def pick_files_for_cards(db: Session, cards: list[Card]) -> dict[int, list[ManagedFile]]:
    try:
        return pick_and_claim_files_for_cards(db, cards)
    except FileClaimConflict as exc:
        db.rollback()
        raise ServiceError("库存状态变化，请重试") from exc


def files_for_card_redemption(db: Session, cards: list[Card]) -> tuple[dict[int, list[ManagedFile]], set[int]]:
    picks_by_card: dict[int, list[ManagedFile]] = {}
    first_redemption_cards: list[Card] = []
    card_ids = [card.id for card in cards]
    originals_by_card: dict[int, Redemption] = {}
    if card_ids:
        originals = db.scalars(
            select(Redemption)
            .where(Redemption.card_id.in_(card_ids), Redemption.status == "completed")
            .order_by(Redemption.card_id.asc(), Redemption.redeemed_at.asc(), Redemption.id.asc())
        )
        for redemption in originals:
            originals_by_card.setdefault(redemption.card_id, redemption)

    file_ids_by_card: dict[int, list[int]] = {}
    for card in cards:
        original = originals_by_card.get(card.id)
        if not original:
            if card.redemption_count > 1:
                raise ServiceError(f"卡密首次兑换记录不存在，禁止重新绑定账号文件：{card.code}")
            first_redemption_cards.append(card)
            continue
        file_ids_by_card[card.id] = [
            int(item) for item in (original.file_ids or "").split(",") if item.strip().isdigit()
        ]

    all_file_ids = {file_id for file_ids in file_ids_by_card.values() for file_id in file_ids}
    files_by_id = {
        item.id: item for item in db.scalars(select(ManagedFile).where(ManagedFile.id.in_(all_file_ids)))
    }
    for card in cards:
        if card.id not in file_ids_by_card:
            continue
        file_ids = file_ids_by_card[card.id]
        bound_files = [files_by_id[file_id] for file_id in file_ids if file_id in files_by_id]
        if len(file_ids) != card.file_count or len(bound_files) != card.file_count:
            raise ServiceError(f"卡密首次绑定的账号文件不存在：{card.code}")
        picks_by_card[card.id] = bound_files

    if first_redemption_cards:
        picks_by_card.update(pick_files_for_cards(db, first_redemption_cards))
    return picks_by_card, {card.id for card in first_redemption_cards}


def reserve_cards_for_redemption(db: Session, cards: list[Card], timestamp: datetime) -> None:
    # Updating the counter in the same guarded statement makes the usage limit
    # authoritative even when multiple requests redeem the same card at once.
    for card in sorted(cards, key=lambda item: item.id):
        next_count = Card.redemption_count + 1
        result = db.execute(
            update(Card)
            .where(
                Card.id == card.id,
                Card.status == "pending",
                Card.max_redemptions > Card.redemption_count,
                or_(Card.expires_at.is_(None), Card.expires_at > timestamp),
            )
            .values(
                redemption_count=next_count,
                status=case((next_count >= Card.max_redemptions, "sold"), else_="pending"),
                used_at=func.coalesce(Card.used_at, timestamp),
            ),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            db.rollback()
            raise ServiceError("卡密已过期、已使用或状态已变化，请刷新后重试")
    for card in cards:
        db.refresh(card)


def pick_and_claim_files_for_cards(db: Session, cards: list[Card]) -> dict[int, list[ManagedFile]]:
    required_by_creator: dict[int, int] = {}
    for card in cards:
        required_by_creator[card.creator_id] = required_by_creator.get(card.creator_id, 0) + card.file_count
    available_by_creator: dict[int, list[ManagedFile]] = {}
    picks_by_card: dict[int, list[ManagedFile]] = {}
    for card in cards:
        if card.creator_id not in available_by_creator:
            required = required_by_creator[card.creator_id]
            candidate_limit = min(MAX_REDEMPTION_CANDIDATES, max(required, required * 4))
            available_by_creator[card.creator_id] = list(
                db.scalars(
                    select(ManagedFile)
                    .where(ManagedFile.uploader_id == card.creator_id, *normal_inventory_filter())
                    .order_by(ManagedFile.uploaded_at.asc(), ManagedFile.id.asc())
                    .limit(candidate_limit)
                )
            )
        pool = available_by_creator[card.creator_id]
        if len(pool) < card.file_count:
            raise ServiceError(f"库存不足，无法兑换：{card.code}")
        picked = weighted_pick(pool, card.file_count)
        claim_available_files(db, [item.id for item in picked])
        picked_ids = {item.id for item in picked}
        available_by_creator[card.creator_id] = [item for item in pool if item.id not in picked_ids]
        picks_by_card[card.id] = picked
    return picks_by_card


def redeem_card(db: Session, code: str) -> Path:
    return redeem_cards(db, code)


def write_files_zip(output_path: Path, files: list[ManagedFile]) -> None:
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for item in files:
            name = json_download_name(item.original_name)
            if name in used_names:
                name = f"{item.id}-{name}"
            used_names.add(name)
            archive.write(item.stored_path, arcname=name)


def assert_delivery_size(files: list[ManagedFile]) -> None:
    total_bytes = 0
    for item in files:
        try:
            file_size = Path(item.stored_path).stat().st_size
        except OSError as exc:
            raise ServiceError(f"账号文件无法读取：{item.original_name}") from exc
        if file_size > MAX_JSON_DOCUMENT_BYTES:
            raise ResourceLimitError(f"账号文件过大，无法交付：{item.original_name}")
        total_bytes += file_size
        if total_bytes > MAX_REDEEM_BYTES:
            raise ResourceLimitError("单次兑换文件总大小不能超过 25MB，请分批兑换")


def redeem_cards(db: Session, raw_codes: str) -> Path:
    codes = parse_card_codes(raw_codes)
    timestamp = now_utc()
    cards_by_code = {card.code: card for card in db.scalars(select(Card).where(Card.code.in_(codes)))}
    cards: list[Card] = []
    for code in codes:
        cards.append(assert_card_can_redeem(cards_by_code.get(code), code, timestamp))

    total_count = sum(card.file_count for card in cards)
    if total_count > MAX_REDEEM_FILES:
        raise ResourceLimitError(f"单次最多支持打包 {MAX_REDEEM_FILES} 个文件")

    output_path: Path | None = None
    try:
        reserve_cards_for_redemption(db, cards, timestamp)
        picks_by_card, first_redemption_card_ids = files_for_card_redemption(db, cards)

        picked_files = [item for picked in picks_by_card.values() for item in picked]
        assert_delivery_size(picked_files)
        first_redemption_files = [
            item for card in cards if card.id in first_redemption_card_ids for item in picks_by_card[card.id]
        ]
        for item in first_redemption_files:
            item.status = "locked"
        db.flush()
        output_stem = cards[0].code if len(cards) == 1 else f"CDK_BATCH_{len(cards)}_CARDS"
        is_single_json = len(cards) == 1 and len(picked_files) == 1
        suffix = "json" if is_single_json else "zip"
        output_path = DOWNLOAD_DIR / f"{output_stem}_{timestamp.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}.{suffix}"
        if is_single_json:
            shutil.copyfile(picked_files[0].stored_path, output_path)
        else:
            write_files_zip(
                output_path,
                [item for card in cards for item in picks_by_card[card.id]],
            )
        for item in picked_files:
            item.latest_download_at = timestamp
        for item in first_redemption_files:
            item.status = "sold"
            item.sold_at = timestamp
        for card in cards:
            if card.id in first_redemption_card_ids:
                for item in picks_by_card[card.id]:
                    item.sold_card_id = card.id
            redemption = Redemption(
                card_id=card.id,
                redeemed_at=timestamp,
                output_format="cpa",
                download_path=str(output_path),
                file_ids=",".join(str(item.id) for item in picks_by_card[card.id]),
                status="completed",
            )
            db.add(redemption)
            add_audit(db, None, "redeem_card", "card", card.id, card.code)
        db.commit()
        return output_path
    except Exception:
        db.rollback()
        if output_path and output_path.exists():
            output_path.unlink()
        raise


def sub2api_download_name(timestamp: datetime) -> str:
    return f"{SUB2API_DOWNLOAD_PREFIX}-{timestamp.strftime('%Y%m%d%H%M%S')}-plus.json"


def sub2api_storage_path(timestamp: datetime) -> Path:
    unique_suffix = uuid.uuid4().hex
    return DOWNLOAD_DIR / f"{SUB2API_DOWNLOAD_PREFIX}-{timestamp.strftime('%Y%m%d%H%M%S')}-{unique_suffix}.json"


def redeem_cards_as_sub2api(db: Session, raw_codes: str) -> tuple[Path, str]:
    codes = parse_card_codes(raw_codes)
    timestamp = now_utc()
    cards_by_code = {card.code: card for card in db.scalars(select(Card).where(Card.code.in_(codes)))}
    cards: list[Card] = []
    for code in codes:
        cards.append(assert_card_can_redeem(cards_by_code.get(code), code, timestamp))

    total_count = sum(card.file_count for card in cards)
    if total_count > MAX_REDEEM_FILES:
        raise ResourceLimitError(f"单次最多支持打包 {MAX_REDEEM_FILES} 个文件")

    output_path: Path | None = None
    try:
        reserve_cards_for_redemption(db, cards, timestamp)
        picks_by_card, first_redemption_card_ids = files_for_card_redemption(db, cards)

        picked_files = [item for picked in picks_by_card.values() for item in picked]
        assert_delivery_size(picked_files)
        first_redemption_files = [
            item for card in cards if card.id in first_redemption_card_ids for item in picks_by_card[card.id]
        ]
        multi_file_delivery = len(cards) > 1 or total_count > 1
        output_path = (
            DOWNLOAD_DIR / f"{SUB2API_DOWNLOAD_PREFIX}-{timestamp.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.zip"
            if multi_file_delivery
            else sub2api_storage_path(timestamp)
        )
        for item in first_redemption_files:
            item.status = "locked"
        db.flush()

        sub2api_data = build_sub2api_config(picked_files)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if multi_file_delivery:
            inner_name = sub2api_download_name(timestamp)
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(inner_name, json_bytes(sub2api_data))
        else:
            with output_path.open("w", encoding="utf-8") as file:
                json.dump(sub2api_data, file, indent=2, ensure_ascii=False)
                file.write("\n")

        for item in picked_files:
            item.latest_download_at = timestamp
        for item in first_redemption_files:
            item.status = "sold"
            item.sold_at = timestamp
        for card in cards:
            if card.id in first_redemption_card_ids:
                for item in picks_by_card[card.id]:
                    item.sold_card_id = card.id
            redemption = Redemption(
                card_id=card.id,
                redeemed_at=timestamp,
                output_format="sub",
                download_path=str(output_path),
                file_ids=",".join(str(item.id) for item in picks_by_card[card.id]),
                status="completed",
            )
            db.add(redemption)
            add_audit(db, None, "redeem_card_sub2api", "card", card.id, card.code)
        db.commit()
        download_name = (
            f"{SUB2API_DOWNLOAD_PREFIX}-{timestamp.strftime('%Y%m%d%H%M%S')}-{len(picked_files)}-accounts.zip"
            if multi_file_delivery
            else sub2api_download_name(timestamp)
        )
        return output_path, download_name
    except Exception:
        db.rollback()
        if output_path and output_path.exists():
            output_path.unlink()
        raise


def rebuild_redemption_download(db: Session, redemption: Redemption) -> tuple[Path, str, str]:
    file_ids = [int(item) for item in (redemption.file_ids or "").split(",") if item.strip().isdigit()]
    files_by_id = {item.id: item for item in db.scalars(select(ManagedFile).where(ManagedFile.id.in_(file_ids)))}
    files = [files_by_id[file_id] for file_id in file_ids if file_id in files_by_id]
    if not files:
        raise ServiceError("兑换记录关联的账号文件不存在")

    timestamp = now_utc()
    unique = uuid.uuid4().hex
    if redemption.output_format == "sub":
        output_path = DOWNLOAD_DIR / f"redemption-{redemption.id}-sub-{unique}.json"
        output_path.write_bytes(json_bytes(build_sub2api_config(files)))
        download_name = sub2api_download_name(timestamp)
        media_type = "application/json"
    elif len(files) == 1:
        output_path = DOWNLOAD_DIR / f"redemption-{redemption.id}-cpa-{unique}.json"
        shutil.copyfile(files[0].stored_path, output_path)
        download_name = json_download_name(files[0].original_name)
        media_type = "application/json"
    else:
        output_path = DOWNLOAD_DIR / f"redemption-{redemption.id}-cpa-{unique}.zip"
        write_files_zip(output_path, files)
        download_name = f"{redemption.card.code}-{len(files)}-accounts.zip"
        media_type = "application/zip"

    redemption.download_path = str(output_path)
    add_audit(db, None, "rebuild_redemption_download", "redemption", redemption.id, redemption.card.code)
    db.commit()
    return output_path, download_name, media_type


def lookup_card_download(
    db: Session,
    raw_code: str,
    timestamp: datetime | None = None,
) -> tuple[Path, str, str, Redemption]:
    codes = parse_card_codes(raw_code)
    if len(codes) != 1:
        raise ServiceError("查找原文件时每次只能输入一个 CDK")
    timestamp = timestamp or now_utc()
    card = db.scalar(select(Card).where(Card.code == codes[0]))
    if not card:
        raise ServiceError("未找到该兑换码的兑换记录")
    if card.status == "voided":
        raise ServiceError("卡密已禁用，无法查找原文件")
    if card.expires_at is not None and card.expires_at <= timestamp:
        raise ServiceError("卡密已过期，无法查找原文件")
    original = db.scalar(
        select(Redemption)
        .where(Redemption.card_id == card.id, Redemption.status == "completed")
        .order_by(Redemption.redeemed_at.asc(), Redemption.id.asc())
        .limit(1)
    )
    if not original:
        raise ServiceError("该兑换码尚未完成兑换")
    output_path, download_name, media_type = rebuild_redemption_download(db, original)
    add_audit(db, None, "lookup_card_download", "card", card.id, card.code)
    db.commit()
    return output_path, download_name, media_type, original


def void_files(db: Session, actor: User, ids: list[int]) -> int:
    if not ids:
        return 0
    query = select(ManagedFile).where(ManagedFile.id.in_(ids), ManagedFile.status.in_(["available", "locked"]))
    if actor.role != ROLE_SUPER_ADMIN:
        query = query.where(ManagedFile.uploader_id == actor.id)
    files = list(db.scalars(query))
    timestamp = now_utc()
    for item in files:
        item.status = "voided"
        item.voided_at = timestamp
        add_audit(db, actor.id, "void_file", "file", item.id, item.original_name)
    return len(files)


def void_cards(db: Session, actor: User, ids: list[int]) -> int:
    if not ids:
        return 0
    query = select(Card).where(Card.id.in_(ids), Card.status == "pending")
    if actor.role != ROLE_SUPER_ADMIN:
        query = query.where(Card.creator_id == actor.id)
    cards = list(db.scalars(query))
    timestamp = now_utc()
    for card in cards:
        card.status = "voided"
        card.voided_at = timestamp
        add_audit(db, actor.id, "void_card", "card", card.id, card.code)
    return len(cards)


def reset_storage_for_tests() -> None:
    for directory in (UPLOAD_DIR, DOWNLOAD_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
