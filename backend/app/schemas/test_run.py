import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, HttpUrl, field_validator

from app.schemas.test_result import TestResultRead


class TestRunCreate(BaseModel):
    target_url: str
    requires_auth: bool = False

    @field_validator("target_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("target_url must start with http:// or https://")
        return v.rstrip("/")


class TestRunRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    target_url: str
    input_filename: str
    requires_auth: bool
    status: str
    error_message: Optional[str]
    total_tests: Optional[int]
    passed_count: int
    failed_count: int
    manual_count: int
    skipped_count: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    completed_tests: int
    results: List[TestResultRead] = []

    model_config = {"from_attributes": True}


class TestRunSummary(BaseModel):
    """Lightweight run list item — no nested results."""
    id: uuid.UUID
    created_at: datetime
    target_url: str
    input_filename: str
    requires_auth: bool
    status: str
    total_tests: Optional[int]
    passed_count: int
    failed_count: int
    manual_count: int
    skipped_count: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    completed_tests: int

    model_config = {"from_attributes": True}
