import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestRun, TestResult, RunEvent


async def create_run(
    db: AsyncSession,
    *,
    target_url: str,
    input_filename: str,
    input_file_path: str,
    requires_auth: bool,
    total_tests: Optional[int] = None,
) -> TestRun:
    run = TestRun(
        target_url=target_url,
        input_filename=input_filename,
        input_file_path=input_file_path,
        requires_auth=requires_auth,
        total_tests=total_tests,
        status="pending",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> Optional[TestRun]:
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    return result.scalar_one_or_none()


async def list_runs(db: AsyncSession, limit: int = 50, offset: int = 0) -> List[TestRun]:
    result = await db.execute(
        select(TestRun).order_by(TestRun.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def update_run_status(
    db: AsyncSession,
    run: TestRun,
    status: str,
    *,
    error_message: Optional[str] = None,
) -> TestRun:
    run.status = status
    if error_message is not None:
        run.error_message = error_message
    await db.commit()
    await db.refresh(run)
    return run


async def set_celery_task_id(
    db: AsyncSession, run: TestRun, celery_task_id: str
) -> TestRun:
    run.celery_task_id = celery_task_id
    await db.commit()
    await db.refresh(run)
    return run


async def set_auth_state_path(
    db: AsyncSession, run: TestRun, auth_state_path: str
) -> TestRun:
    run.auth_state_path = auth_state_path
    await db.commit()
    await db.refresh(run)
    return run


async def set_output_file_path(
    db: AsyncSession, run: TestRun, output_file_path: str
) -> TestRun:
    run.output_file_path = output_file_path
    await db.commit()
    await db.refresh(run)
    return run
