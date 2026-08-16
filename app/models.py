from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
ADMIN_ROLES = frozenset({ROLE_ADMIN, ROLE_SUPER_ADMIN})
PRODUCT_DRAFT = "draft"
PRODUCT_LISTED = "listed"
PRODUCT_HIDDEN = "hidden"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    quota_pool_base_url: Mapped[str] = mapped_column(String(500), default="")
    quota_pool_management_key: Mapped[str] = mapped_column(Text, default="")
    liveness_pool_base_url: Mapped[str] = mapped_column(String(500), default="")
    liveness_pool_management_key: Mapped[str] = mapped_column(Text, default="")
    liveness_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    files = relationship("ManagedFile", back_populates="uploader")
    cards = relationship("Card", back_populates="creator")
    products = relationship("Product", back_populates="creator")

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES

    @property
    def is_super_admin(self) -> bool:
        return self.role == ROLE_SUPER_ADMIN


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=PRODUCT_DRAFT, index=True)
    health_check_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    health_timeout_seconds: Mapped[int] = mapped_column(Integer, default=15)
    health_daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=3)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", back_populates="products")
    files = relationship("ManagedFile", back_populates="product")
    cards = relationship("Card", back_populates="product")
    redemptions = relationship("Redemption", back_populates="product")


class ManagedFile(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255), index=True)
    stored_path: Mapped[str] = mapped_column(String(500))
    generated_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    latest_download_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    account_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    account_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    account_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_error_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    batch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sold_card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"), nullable=True, index=True)
    source_format: Mapped[str] = mapped_column(String(20), default="cpa", index=True)
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)

    uploader = relationship("User", back_populates="files")
    product = relationship("Product", back_populates="files")
    sold_card = relationship("Card")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    file_count: Mapped[int] = mapped_column(Integer, default=1)
    max_redemptions: Mapped[int] = mapped_column(Integer, default=1)
    redemption_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    creator = relationship("User", back_populates="cards")
    product = relationship("Product", back_populates="cards")
    redemptions = relationship("Redemption", back_populates="card")


class Redemption(Base):
    __tablename__ = "redemptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    output_format: Mapped[str] = mapped_column(String(20), default="cpa", index=True)
    download_path: Mapped[str] = mapped_column(String(500))
    file_ids: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="completed", index=True)

    card = relationship("Card", back_populates="redemptions")
    product = relationship("Product", back_populates="redemptions")
    download_links = relationship("TemporaryDownload", back_populates="redemption")


class TemporaryDownload(Base):
    __tablename__ = "temporary_downloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    redemption_id: Mapped[int | None] = mapped_column(ForeignKey("redemptions.id"), nullable=True, index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    download_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(40), default="redeem", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_download_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    redemption = relationship("Redemption", back_populates="download_links")


class SecurityAttempt(Base):
    __tablename__ = "security_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    subject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
