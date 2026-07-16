from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx


CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_REQUEST_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
    "Content-Type": "application/json",
    "User-Agent": "codex_cli_rs/0.76.0",
}
REQUEST_TIMEOUT_SECONDS = 60


@dataclass
class QuotaWindow:
    id: str
    label: str
    remaining_percent: int | None
    reset_at: datetime | None
    reset_label: str
    status: str


@dataclass
class CodexQuotaCard:
    name: str
    email: str
    provider: str
    plan_type: str
    status: str
    status_message: str
    auth_index: str | None
    windows: list[QuotaWindow]


class CLIProxyError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_management_url(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url is required")
    if not base.endswith("/v0/management"):
        base = f"{base}/v0/management"
    return urljoin(f"{base}/", path.lstrip("/"))


def clean_json_filename(filename: str) -> str:
    clean_name = filename.strip().replace("\\", "/").split("/")[-1]
    if not clean_name.lower().endswith(".json"):
        raise ValueError("只支持 .json 文件")
    return clean_name


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _reset_label(value: datetime | None) -> str:
    if not value:
        return "-"
    return value.astimezone().strftime("%m/%d %H:%M")


def _normalize_plan(value: Any) -> str:
    text = _string(value)
    if not text:
        return "Free"
    mapping = {
        "free": "Free",
        "plus": "Plus",
        "pro": "Pro",
        "pro_lite": "Pro Lite",
        "prolite": "Pro Lite",
        "team": "Team",
    }
    return mapping.get(text.lower().replace("-", "_"), text[:1].upper() + text[1:])


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _window_seconds(window: dict[str, Any] | None) -> int | None:
    if not window:
        return None
    raw = _number(window.get("limit_window_seconds") or window.get("limitWindowSeconds"))
    return int(raw) if raw is not None else None


def _pick_windows(limit_info: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not limit_info:
        return None, None
    primary = limit_info.get("primary_window") or limit_info.get("primaryWindow")
    secondary = limit_info.get("secondary_window") or limit_info.get("secondaryWindow")
    candidates = [item for item in (primary, secondary) if isinstance(item, dict)]
    five_hour = next((item for item in candidates if _window_seconds(item) == 18000), None)
    weekly = next((item for item in candidates if _window_seconds(item) == 604800), None)
    if five_hour is None and isinstance(primary, dict) and primary is not weekly:
        five_hour = primary
    if weekly is None and isinstance(secondary, dict) and secondary is not five_hour:
        weekly = secondary
    return five_hour, weekly


def _make_window(id_: str, label: str, window: dict[str, Any] | None, limit_info: dict[str, Any] | None) -> QuotaWindow | None:
    if not isinstance(window, dict):
        return None
    used = _number(_first_present(window, "used_percent", "usedPercent"))
    allowed = limit_info.get("allowed") if isinstance(limit_info, dict) else None
    limit_reached = (limit_info.get("limit_reached") or limit_info.get("limitReached")) if isinstance(limit_info, dict) else False
    if used is None and (allowed is False or limit_reached):
        used = 100
    remaining = None if used is None else max(0, min(100, int(round(100 - used))))
    reset_at = _parse_datetime(window.get("resets_at") or window.get("resetsAt") or window.get("reset_at") or window.get("resetAt"))
    status = "empty" if remaining == 0 else "ok" if remaining is not None and remaining >= 30 else "warn"
    return QuotaWindow(id_, label, remaining, reset_at, _reset_label(reset_at), status)


def normalize_codex_quota(payload: dict[str, Any] | str | None) -> tuple[str, list[QuotaWindow]]:
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip() else None
    if not isinstance(payload, dict) or not payload:
        raise ValueError("empty quota payload")

    windows: list[QuotaWindow] = []
    rate_limit = payload.get("rate_limit") or payload.get("rateLimit")
    if isinstance(rate_limit, dict):
        five_hour, weekly = _pick_windows(rate_limit)
        windows.extend(
            item
            for item in (
                _make_window("five-hour", "5小时额度", five_hour, rate_limit),
                _make_window("weekly", "周额度", weekly, rate_limit),
            )
            if item
        )

    additional = payload.get("additional_rate_limits") or payload.get("additionalRateLimits") or []
    if isinstance(additional, list):
        for index, item in enumerate(additional):
            if not isinstance(item, dict):
                continue
            limit = item.get("rate_limit") or item.get("rateLimit")
            if not isinstance(limit, dict):
                continue
            name = _string(item.get("limit_name") or item.get("limitName") or item.get("metered_feature")) or f"附加{index + 1}"
            primary = limit.get("primary_window") or limit.get("primaryWindow")
            secondary = limit.get("secondary_window") or limit.get("secondaryWindow")
            windows.extend(
                window
                for window in (
                    _make_window(f"additional-{index}-primary", f"{name} 主要额度", primary, limit),
                    _make_window(f"additional-{index}-secondary", f"{name} 周期额度", secondary, limit),
                )
                if window
            )

    if not windows:
        raise ValueError("empty quota windows")
    return _normalize_plan(payload.get("plan_type") or payload.get("planType")), windows


def _jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    for candidate in (text, text[text.find("{") :] if "{" in text else text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            return ast.literal_eval(candidate)
        except (ValueError, SyntaxError):
            pass
    return value


def _usage_limit_error(value: Any) -> dict[str, Any] | None:
    body = value.get("body") if isinstance(value, dict) and "body" in value else value
    body = _jsonish(body)
    if not isinstance(body, dict):
        return None
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    if not isinstance(error, dict):
        return None
    error_type = _string(error.get("type") or error.get("code"))
    return error if error_type == "usage_limit_reached" else None


def _api_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, (dict, list)):
            return json.dumps({"error": error}, ensure_ascii=False)
        return str(data.get("message") or error or f"HTTP {response.status_code}")
    return f"HTTP {response.status_code}"


def _result_error(result: dict[str, Any]) -> str:
    status = result.get("status_code") or result.get("statusCode")
    body = _jsonish(result.get("body"))
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return f"{status} {error.get('message') or error}"
        return f"{status} {error or body.get('message') or 'Request failed'}"
    return f"{status} {body or 'Request failed'}"


class CLIProxyClient:
    def __init__(self, base_url: str, management_key: str) -> None:
        self.base_url = base_url
        self.management_key = management_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.management_key}"}

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(build_management_url(self.base_url, path), headers=self._headers())
        if response.status_code >= 400:
            raise CLIProxyError(_api_error(response), response.status_code)
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                build_management_url(self.base_url, path),
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise CLIProxyError(_api_error(response), response.status_code)
        return response.json()

    async def _post_multipart(self, path: str, files: dict[str, tuple[str, bytes, str]]) -> Any:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(build_management_url(self.base_url, path), headers=self._headers(), files=files)
        if response.status_code >= 400:
            raise CLIProxyError(_api_error(response), response.status_code)
        return response.json()

    async def _delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.delete(build_management_url(self.base_url, path), headers=self._headers(), params=params)
        if response.status_code >= 400:
            raise CLIProxyError(_api_error(response), response.status_code)
        return response.json()

    async def auth_files(self) -> list[dict[str, Any]]:
        payload = await self._get("/auth-files")
        files = payload.get("files") if isinstance(payload, dict) else []
        return files if isinstance(files, list) else []

    async def auth_file(self, filename: str) -> dict[str, Any] | None:
        clean_name = clean_json_filename(filename)
        for file in await self.auth_files():
            name = _string(file.get("name") or file.get("id"))
            if name == clean_name:
                return file
        return None

    async def upload_auth_file(self, filename: str, content: bytes) -> Any:
        clean_name = clean_json_filename(filename)
        json.loads(content.decode("utf-8"))
        return await self._post_multipart("/auth-files", {"file": (clean_name, content, "application/json")})

    async def delete_auth_file(self, filename: str) -> Any:
        return await self._delete("/auth-files", {"name": clean_json_filename(filename)})

    async def codex_cards(self) -> list[CodexQuotaCard]:
        cards = []
        for file in await self.auth_files():
            provider = str(file.get("provider") or file.get("type") or "").strip().lower()
            if provider != "codex":
                continue
            cards.append(await self._codex_card(file))
        return cards

    async def codex_card_for_auth_file(self, filename: str) -> CodexQuotaCard | None:
        file = await self.auth_file(filename)
        if not file:
            return None
        provider = str(file.get("provider") or file.get("type") or "").strip().lower()
        if provider != "codex":
            return CodexQuotaCard(clean_json_filename(filename), clean_json_filename(filename), provider or "unknown", "Free", "error", "不是 Codex 账号", None, [])
        return await self._codex_card(file)

    async def _codex_card(self, file: dict[str, Any]) -> CodexQuotaCard:
        name = _string(file.get("name") or file.get("id")) or "unknown.json"
        email = _string(file.get("email") or file.get("account")) or name
        provider = _string(file.get("provider") or file.get("type")) or "codex"
        auth_index = _string(file.get("auth_index") or file.get("authIndex"))
        if not auth_index:
            return CodexQuotaCard(name, email, provider, "Free", "error", "缺少 auth_index", None, [])
        try:
            result = await self._post(
                "/api-call",
                {
                    "auth_index": auth_index,
                    "method": "GET",
                    "url": CODEX_USAGE_URL,
                    "header": CODEX_REQUEST_HEADERS,
                },
            )
            status_code = int(result.get("status_code") or result.get("statusCode") or 0)
            if _usage_limit_error(result):
                return CodexQuotaCard(name, email, provider, "Free", "exhausted", "额度用完", auth_index, [])
            if status_code == 401:
                return CodexQuotaCard(name, email, provider, "Free", "unavailable", "不可用", auth_index, [])
            if status_code < 200 or status_code >= 300:
                raise CLIProxyError(_result_error(result), status_code)
            plan_type, windows = normalize_codex_quota(result.get("body"))
            return CodexQuotaCard(name, email, provider, plan_type, "success", "使用中", auth_index, windows)
        except Exception as exc:
            if _usage_limit_error(str(exc)):
                return CodexQuotaCard(name, email, provider, "Free", "exhausted", "额度用完", auth_index, [])
            return CodexQuotaCard(name, email, provider, "Free", "error", str(exc) or "额度读取失败", auth_index, [])
