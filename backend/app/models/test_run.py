import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False
    )

    # Run configuration
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    input_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    input_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    output_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Auth
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auth_state_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # State machine: pending → running → completed | failed | timed_out | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Counters (updated by worker as tests complete)
    total_tests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    results: Mapped[list["TestResult"]] = relationship(  # noqa: F821
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list["RunEvent"]] = relationship(  # noqa: F821
        back_populates="run", cascade="all, delete-orphan"
    )

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def completed_tests(self) -> int:
        return self.passed_count + self.failed_count + self.manual_count + self.skipped_count
