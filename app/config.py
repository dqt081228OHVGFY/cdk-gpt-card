from pathlib import Path
import configparser
import os
from urllib.parse import urlsplit


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.getenv("TIKAWANG_CONFIG_FILE", BASE_DIR / "config.ini"))


def _read_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        parser.read(CONFIG_FILE, encoding="utf-8")
    return parser


_config = _read_config()


def get_setting(section: str, key: str, default: str) -> str:
    env_key = f"TIKAWANG_{section}_{key}".upper()
    if env_key in os.environ:
        return os.environ[env_key]
    if _config.has_option(section, key):
        return _config.get(section, key)
    return default


def get_database_url() -> str:
    env_url = os.getenv("TIKAWANG_DATABASE_URL")
    if env_url:
        return env_url
    return get_setting("database", "url", f"sqlite:///{BASE_DIR / 'data' / 'tikawang.db'}")


def get_storage_dir() -> Path:
    return Path(get_setting("storage", "dir", str(BASE_DIR / "storage")))


def get_session_secret() -> str:
    secret = get_setting("security", "session_secret", "tikawang-local-secret-change-me").strip()
    if len(secret) < 32 or secret in {
        "tikawang-local-secret-change-me",
        "replace-with-at-least-32-random-characters",
    }:
        raise RuntimeError("security.session_secret 必须设置为至少 32 位的随机密钥")
    return secret


def get_super_admin_username() -> str:
    return get_setting("security", "super_admin_username", "").strip()


def get_super_admin_password() -> str:
    return get_setting("security", "super_admin_password", "")


def get_admin_username() -> str:
    return get_setting("security", "admin_username", "").strip()


def get_admin_password() -> str:
    return get_setting("security", "admin_password", "")


def trust_proxy_headers() -> bool:
    return get_setting("security", "trust_proxy_headers", "false").strip().lower() in {"1", "true", "yes", "on"}


def get_cookie_secure() -> bool:
    return get_setting("security", "cookie_secure", "false").strip().lower() in {"1", "true", "yes", "on"}


def get_public_base_url() -> str:
    value = get_setting("server", "public_base_url", "https://cdk.ambition.qzz.io").rstrip("/")
    parsed = urlsplit(value)
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if not parsed.netloc or (parsed.scheme != "https" and not is_local_http):
        raise RuntimeError("server.public_base_url 必须是 HTTPS 地址；仅本机开发允许 HTTP")
    return value


def get_liveness_dashboard_url() -> str:
    value = get_setting(
        "liveness",
        "dashboard_url",
        "https://api.ambition.qzz.io/admin/cdk-liveness",
    ).strip().rstrip("/")
    parsed = urlsplit(value)
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if not parsed.netloc or (parsed.scheme != "https" and not is_local_http):
        raise RuntimeError("liveness.dashboard_url 必须是 HTTPS 地址；仅本机开发允许 HTTP")
    return value


def get_codex_models_url() -> str:
    return get_setting("codex", "models_url", "https://chatgpt.com/backend-api/codex/models").strip()


def get_codex_token_url() -> str:
    return get_setting("codex", "token_url", "https://auth.openai.com/oauth/token").strip()


def get_codex_oauth_client_id() -> str:
    return get_setting("codex", "oauth_client_id", "app_EMoamEEZ73f0CkXaXp7hrann").strip()


def get_codex_client_version() -> str:
    return get_setting("codex", "client_version", "0.144.1").strip()


def get_codex_outbound_proxy_url() -> str:
    return get_setting("codex", "outbound_proxy_url", "").strip()
