import secrets

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Card, User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
CARD_TOKEN_BYTES = 16
DUMMY_PASSWORD_HASH = pwd_context.hash("timing-only-invalid-account-password")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
    if not user:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def generate_card_code(db: Session) -> str:
    while True:
        code = secrets.token_hex(CARD_TOKEN_BYTES)
        exists = db.scalar(select(Card.id).where(Card.code == code))
        if not exists:
            return code
