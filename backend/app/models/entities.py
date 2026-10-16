from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


def now_utc():
    return datetime.now(timezone.utc)


class Recruiter(Base):
    __tablename__ = "recruiters"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), index=True)
    job_role: Mapped[str] = mapped_column(String(160))
    consent_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("recruiters.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("recruiters.id"))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    candidate: Mapped[Candidate] = relationship()


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(180))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (UniqueConstraint("created_by", "interview_id", "audio_sha256", "operation", "config_version", name="uq_analysis_content_operation_config"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("recruiters.id"), index=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    audio_sha256: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(50))
    config_version: Mapped[str] = mapped_column(String(80), default="detectif-default")
    status: Mapped[str] = mapped_column(String(24), default="processing")
    provider_operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("recruiters.id"), index=True)
    interview_id: Mapped[int | None] = mapped_column(ForeignKey("interviews.id", ondelete="SET NULL"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(24))
    credits_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_reserved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    provider_operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("recruiters.id"))
    decision: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    recruiter: Mapped[Recruiter] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("recruiters.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
