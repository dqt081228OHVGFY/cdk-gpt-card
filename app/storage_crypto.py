from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_session_secret


ENCRYPTED_PREFIX = b"TKWENC1\n"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    digest = hashlib.sha256(get_session_secret().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_bytes(raw: bytes) -> bytes:
    return ENCRYPTED_PREFIX + _fernet().encrypt(raw)


def decrypt_bytes(raw: bytes) -> bytes:
    if not raw.startswith(ENCRYPTED_PREFIX):
        return raw
    try:
        return _fernet().decrypt(raw[len(ENCRYPTED_PREFIX) :])
    except InvalidToken as exc:
        raise ValueError("账号文件解密失败，请检查服务密钥是否变化") from exc


def read_file(path: str | Path) -> bytes:
    return decrypt_bytes(Path(path).read_bytes())


def write_file(path: str | Path, raw: bytes) -> None:
    Path(path).write_bytes(encrypt_bytes(raw))
