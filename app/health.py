from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .config import (
    get_codex_client_version,
    get_codex_models_url,
    get_codex_oauth_client_id,
    get_codex_outbound_proxy_url,
    get_codex_token_url,
)


HEALTH_ALIVE = "available"
HEALTH_DEAD = "unavailable"
HEALTH_UNKNOWN = "unknown"
HEALTH_CHECKING = "checking"
UNKNOWN_HEALTH_COOLDOWN = timedelta(minutes=10)
HEALTH_REFRESH_SKEW = timedelta(seconds=30)
HEALTH_RESPONSE_LIMIT = 8 * 1024 * 1024
DEFAULT_CODEX_ORIGINATOR = "codex-tui"


@dataclass
class HealthResult:
    status: str
    error: str = ""
    credential: bytes | None = None
    refreshed: bool = False


@dataclass
class CodexCredential:
    root: dict[str, Any]
    token_target: dict[str, Any]
    extra_target: dict[str, Any] | None
    is_sub: bool
    has_nested_tokens: bool
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str
    user_id: str
    expires_at: datetime | None


_credential_locks_guard = threading.Lock()
_credential_locks: dict[str, threading.Lock] = {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_non_empty(*values: str) -> str:
    for value in values:
        value = _string(value)
        if value:
            return value
    return ""


def _truncate(value: str, limit: int = 300) -> str:
    value = (value or "").strip()
    return value[:limit]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), timezone.utc)
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("token is not a JWT")
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = base64.urlsafe_b64decode(segment.encode("ascii"))
    data = json.loads(payload.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _auth_info(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("https://api.openai.com/auth")
    return value if isinstance(value, dict) else {}


def _credential_identity_key(account_id: str, user_id: str, refresh_token: str) -> str:
    account_id = account_id.strip().lower()
    user_id = user_id.strip().lower()
    if account_id and user_id:
        return f"account:{account_id}\x00user:{user_id}"
    if account_id:
        return f"account:{account_id}"
    return f"refresh:{hash(refresh_token)}" if refresh_token else "anonymous"


def _credential_lock(key: str) -> threading.Lock:
    with _credential_locks_guard:
        lock = _credential_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _credential_locks[key] = lock
        return lock


def parse_codex_credential(raw: bytes) -> CodexCredential:
    if not raw.strip():
        raise ValueError("库存 JSON 为空")
    try:
        root = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("库存不是有效 JSON") from exc
    if not isinstance(root, dict):
        raise ValueError("库存 JSON 根节点必须是对象")

    token_target: dict[str, Any] = root
    extra_target: dict[str, Any] | None = None
    is_sub = False
    has_nested_tokens = False
    account_id = _first_non_empty(_string(root.get("account_id")), _string(root.get("chatgpt_account_id")))

    accounts = root.get("accounts")
    if accounts is not None:
        if not isinstance(accounts, list) or len(accounts) != 1 or not isinstance(accounts[0], dict):
            raise ValueError("测活库存 SUB 必须只包含一个账号")
        account = accounts[0]
        credentials = account.get("credentials")
        if not isinstance(credentials, dict):
            raise ValueError("SUB 账号缺少 credentials")
        token_target = credentials
        extra_target = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        account["extra"] = extra_target
        is_sub = True
        account_id = _first_non_empty(
            _string(credentials.get("chatgpt_account_id")),
            _string(credentials.get("account_id")),
            account_id,
        )
    elif isinstance(root.get("tokens"), dict):
        token_target = root["tokens"]
        has_nested_tokens = True
        account_id = _first_non_empty(_string(token_target.get("account_id")), account_id)

    access_token = _string(token_target.get("access_token"))
    refresh_token = _string(token_target.get("refresh_token"))
    id_token = _string(token_target.get("id_token"))
    if not access_token and not refresh_token:
        raise ValueError("库存缺少 access_token 和 refresh_token")

    user_id = ""
    expires_at = None
    if access_token:
        try:
            payload = _decode_jwt_payload(access_token)
            auth = _auth_info(payload)
            account_id = _first_non_empty(_string(auth.get("chatgpt_account_id")), account_id)
            user_id = _first_non_empty(_string(auth.get("chatgpt_user_id")), _string(payload.get("sub")), user_id)
            exp = payload.get("exp")
            if isinstance(exp, (int, float)) and exp > 0:
                expires_at = datetime.fromtimestamp(exp, timezone.utc)
        except Exception:
            pass
    if id_token:
        try:
            payload = _decode_jwt_payload(id_token)
            user_id = _first_non_empty(user_id, _string(payload.get("sub")))
        except Exception:
            pass
    expires_at = (
        _parse_datetime(token_target.get("expires_at"))
        or _parse_datetime(token_target.get("expired"))
        or _parse_datetime(root.get("expires_at"))
        or _parse_datetime(root.get("expired"))
        or expires_at
    )
    return CodexCredential(
        root=root,
        token_target=token_target,
        extra_target=extra_target,
        is_sub=is_sub,
        has_nested_tokens=has_nested_tokens,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id,
        user_id=user_id,
        expires_at=expires_at,
    )


def _apply_refresh(credential: CodexCredential, payload: dict[str, Any], now: datetime) -> None:
    access_token = _string(payload.get("access_token"))
    refresh_token = _string(payload.get("refresh_token")) or credential.refresh_token
    id_token = _string(payload.get("id_token")) or credential.id_token
    expires_in = payload.get("expires_in")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        expires_in = 3600

    credential.access_token = access_token
    credential.refresh_token = refresh_token
    credential.id_token = id_token
    credential.expires_at = now + timedelta(seconds=int(expires_in))
    credential.token_target["access_token"] = access_token
    credential.token_target["refresh_token"] = refresh_token
    if id_token:
        credential.token_target["id_token"] = id_token

    try:
        claims = _decode_jwt_payload(access_token)
        auth = _auth_info(claims)
        credential.account_id = _first_non_empty(_string(auth.get("chatgpt_account_id")), credential.account_id)
        credential.user_id = _first_non_empty(_string(auth.get("chatgpt_user_id")), _string(claims.get("sub")), credential.user_id)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and exp > 0:
            credential.expires_at = datetime.fromtimestamp(exp, timezone.utc)
    except Exception:
        pass

    assert credential.expires_at is not None
    if credential.is_sub:
        credential.token_target["expires_at"] = int(credential.expires_at.timestamp())
        credential.token_target["expires_in"] = int(expires_in)
        if credential.account_id:
            credential.token_target["chatgpt_account_id"] = credential.account_id
        if credential.extra_target is not None:
            credential.extra_target["last_refresh"] = now.isoformat().replace("+00:00", "Z")
        return

    expired = credential.expires_at.isoformat().replace("+00:00", "Z")
    credential.token_target["expired"] = expired
    credential.token_target["last_refresh"] = now.isoformat().replace("+00:00", "Z")
    if credential.account_id:
        credential.token_target["account_id"] = credential.account_id
    if credential.has_nested_tokens:
        credential.root["access_token"] = credential.access_token
        credential.root["refresh_token"] = credential.refresh_token
        if credential.id_token:
            credential.root["id_token"] = credential.id_token
        credential.root["expired"] = expired
        credential.root["last_refresh"] = now.isoformat().replace("+00:00", "Z")
        if credential.account_id:
            credential.root["account_id"] = credential.account_id


def _marshal_credential(credential: CodexCredential) -> bytes:
    return (json.dumps(credential.root, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_atomic(path: str | Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".inventory-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _valid_http_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def health_client_ready() -> bool:
    proxy_url = get_codex_outbound_proxy_url().strip()
    return _valid_http_url(get_codex_models_url()) and _valid_http_url(get_codex_token_url()) and (
        not proxy_url or _valid_http_url(proxy_url) or proxy_url.startswith("socks5://")
    )


class CodexHealthClient:
    def __init__(
        self,
        *,
        models_url: str | None = None,
        token_url: str | None = None,
        oauth_client_id: str | None = None,
        client_version: str | None = None,
        proxy_url: str | None = None,
        timeout_seconds: int | float | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.models_url = models_url or get_codex_models_url()
        self.token_url = token_url or get_codex_token_url()
        self.oauth_client_id = oauth_client_id or get_codex_oauth_client_id()
        self.client_version = client_version or get_codex_client_version()
        self.proxy_url = proxy_url if proxy_url is not None else get_codex_outbound_proxy_url()
        self.timeout_seconds = max(3.0, min(float(timeout_seconds or 12.0), 60.0))
        self.now = now or _utc_now

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(self.timeout_seconds)}
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def check_path(self, path: str | Path) -> HealthResult:
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError:
            return HealthResult(HEALTH_UNKNOWN, "读取库存凭证失败")
        return self.check(raw, persist=lambda updated: write_atomic(path, updated))

    def check(self, raw: bytes, persist: Callable[[bytes], None] | None = None) -> HealthResult:
        try:
            credential = parse_codex_credential(raw)
        except ValueError as exc:
            return HealthResult(HEALTH_DEAD, str(exc))

        lock_key = _credential_identity_key(credential.account_id, credential.user_id, credential.refresh_token)
        with _credential_lock(lock_key):
            refreshed = False
            now = self.now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            if not credential.access_token or (
                credential.expires_at is not None and credential.expires_at <= now + HEALTH_REFRESH_SKEW
            ):
                result = self._refresh(credential, now)
                if result is not None:
                    return result
                refreshed = True

            kind, error = self._probe_models(credential.access_token, credential.account_id)
            if kind == "unauthorized" and not refreshed:
                result = self._refresh(credential, now)
                if result is not None:
                    return result
                refreshed = True
                kind, error = self._probe_models(credential.access_token, credential.account_id)

            updated = _marshal_credential(credential) if refreshed else None
            if refreshed and persist is not None and updated is not None:
                try:
                    persist(updated)
                except OSError:
                    return HealthResult(HEALTH_UNKNOWN, "保存轮换后的凭证失败", updated, True)

            if kind == "alive":
                return HealthResult(HEALTH_ALIVE, credential=updated, refreshed=refreshed)
            if kind == "unauthorized":
                return HealthResult(HEALTH_DEAD, "Codex Models 在刷新后仍返回 401", updated, refreshed)
            return HealthResult(HEALTH_UNKNOWN, error or "Codex Models 返回未知结果", updated, refreshed)

    def _refresh(self, credential: CodexCredential, now: datetime) -> HealthResult | None:
        if not credential.refresh_token:
            return HealthResult(HEALTH_DEAD, "凭证已过期或未授权，且缺少 refresh_token")
        try:
            with self._client() as client:
                response = client.post(
                    self.token_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self.oauth_client_id,
                        "refresh_token": credential.refresh_token,
                        "scope": "openid profile email",
                    },
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            return HealthResult(HEALTH_UNKNOWN, f"OAuth 刷新请求失败：{_compact_http_error(exc)}")
        body = response.content[: 1024 * 1024 + 1]
        if response.status_code < 200 or response.status_code >= 300:
            code, detail = _oauth_error(body)
            permanent = _permanent_oauth_error(code, detail)
            if permanent:
                return HealthResult(HEALTH_DEAD, f"refresh_token 已确定失效：{permanent}")
            return HealthResult(
                HEALTH_UNKNOWN,
                f"OAuth 刷新暂时失败：HTTP {response.status_code} {_truncate(detail or response.reason_phrase)}",
            )
        try:
            payload = response.json()
        except ValueError:
            return HealthResult(HEALTH_UNKNOWN, "OAuth 刷新响应不是有效 JSON")
        if not isinstance(payload, dict) or not _string(payload.get("access_token")):
            return HealthResult(HEALTH_UNKNOWN, "OAuth 刷新响应缺少 access_token")
        _apply_refresh(credential, payload, now)
        return None

    def _probe_models(self, access_token: str, account_id: str) -> tuple[str, str]:
        if not access_token:
            return "unauthorized", "凭证缺少 access_token"
        last_error = ""
        for attempt in range(2):
            kind, retryable, error = self._probe_models_once(access_token, account_id)
            if not retryable or attempt == 1:
                return kind, error
            last_error = error
            time.sleep(0.25)
        return "unknown", last_error

    def _probe_models_once(self, access_token: str, account_id: str) -> tuple[str, bool, str]:
        version = self.client_version
        try:
            with self._client() as client:
                response = client.get(
                    self.models_url,
                    params={"client_version": version},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "User-Agent": f"codex-tui/{version} (Linux Unknown; x86_64) xterm-256color (codex-tui; {version})",
                        "Originator": DEFAULT_CODEX_ORIGINATOR,
                        "Version": version,
                        **({"Chatgpt-Account-Id": account_id.strip()} if account_id.strip() else {}),
                    },
                )
        except Exception as exc:
            return "unknown", True, f"Codex Models 请求失败：{_compact_http_error(exc)}"
        body = response.content[: HEALTH_RESPONSE_LIMIT + 1]
        if response.status_code == 401:
            return "unauthorized", False, "Codex Models 返回 401"
        if response.status_code < 200 or response.status_code >= 300:
            detail = _response_detail(body)
            retryable = response.status_code == 408 or response.status_code >= 500
            return "unknown", retryable, f"Codex Models 返回 HTTP {response.status_code}{detail}"
        try:
            payload = response.json()
        except ValueError:
            return "unknown", False, "Codex Models 成功响应不是有效 JSON"
        models = payload.get("models") if isinstance(payload, dict) else None
        if isinstance(models, list) and any(isinstance(item, dict) and _string(item.get("slug")) for item in models):
            return "alive", False, ""
        return "unknown", False, "Codex Models 响应没有有效模型"


def _oauth_error(body: bytes) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    code = _string(payload.get("error") or payload.get("code")).lower()
    detail = _first_non_empty(_string(payload.get("error_description")), _string(payload.get("message")), code)
    nested = payload.get("error")
    if isinstance(nested, dict):
        code = _first_non_empty(_string(nested.get("code")), _string(nested.get("type")), code).lower()
        detail = _first_non_empty(_string(nested.get("message")), detail)
    return code, _truncate(detail)


def _permanent_oauth_error(code: str, detail: str) -> str:
    combined = f"{code} {detail}".lower().strip()
    for candidate in ("refresh_token_reused", "refresh token was reused", "invalid_grant", "access_denied"):
        if candidate in combined:
            return candidate.replace(" ", "_")
    return ""


def _response_detail(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = _first_non_empty(_string(payload.get("message")), _string(payload.get("detail")), _string(payload.get("error")))
    nested = payload.get("error")
    if isinstance(nested, dict):
        detail = _first_non_empty(_string(nested.get("message")), _string(nested.get("code")), detail)
    return f"：{_truncate(detail)}" if detail else ""


def _compact_http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    return _truncate(str(exc) or exc.__class__.__name__)


def health_status_label(value: str | None) -> str:
    return {
        HEALTH_ALIVE: "活",
        HEALTH_DEAD: "死",
        HEALTH_UNKNOWN: "暂时未知",
        HEALTH_CHECKING: "检测中",
    }.get(value or "", "未检测")


def health_candidate_eligible(account_status: str | None, checked_at: datetime | None, now: datetime | None = None) -> bool:
    if account_status == HEALTH_DEAD:
        return False
    if account_status == HEALTH_UNKNOWN:
        if checked_at is None:
            return True
        now = now or datetime.utcnow()
        return checked_at + UNKNOWN_HEALTH_COOLDOWN <= now
    return True
