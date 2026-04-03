import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TestResultRead(BaseModel):
    id: int
    run_id: uuid.UUID
    test_case_id: str
    title: Optional[str]
    status: str
    actual_result: Optional[str]
    comments: Optional[str]
    error_detail: Optional[str]
    requires_auth: bool
    executed_at: Optional[datetime]

    model_config = {"from_attributes": True}
