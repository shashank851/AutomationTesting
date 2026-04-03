"""
POST /api/v1/runs          — create a new test run (multipart: file + JSON fields)
GET  /api/v1/runs          — list recent runs (summaries)
GET  /api/v1/runs/{run_id} — single run detail with results
DELETE /api/v1/runs/{run_id} — cancel / delete a run
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import TestRunCreate, TestRunRead, TestRunSummary
from app.services import file_service, run_service

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.post("", response_model=TestRunSummary, status_code=status.HTTP_201_CREATED)
async def create_run(
    target_url: str = Form(...),
    requires_auth: bool = Form(False),
    file: UploadFile = File(..., description="Excel test-cases file (.xlsx)"),
    db: AsyncSession = Depends(get_db),
):
    # Validate form data via Pydantic
    try:
        validated = TestRunCreate(target_url=target_url, requires_auth=requires_auth)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Only .xlsx files are accepted.")

    run_id = uuid.uuid4()

    # Persist file
    input_file_path = await file_service.save_upload(run_id, file)

    # Validate Excel and count rows
    total_tests = file_service.validate_excel(input_file_path)

    # Output path reserved (written after run completes)
    output_file_path = file_service.results_path(run_id, file.filename)

    # Create DB record with the pre-computed run_id
    from app.models import TestRun

    run = TestRun(
        id=run_id,
        target_url=validated.target_url,
        input_filename=file.filename,
        input_file_path=input_file_path,
        output_file_path=output_file_path,
        requires_auth=validated.requires_auth,
        total_tests=total_tests,
        status="pending",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # If auth is required, hold in pending_auth until auth_state is uploaded.
    # Otherwise dispatch the Celery task immediately.
    if validated.requires_auth:
        run.status = "pending_auth"
        await db.commit()
        await db.refresh(run)
    else:
        from app.workers.tasks import execute_test_run
        task = execute_test_run.delay(str(run.id))
        await run_service.set_celery_task_id(db, run, task.id)

    return run


@router.get("", response_model=List[TestRunSummary])
async def list_runs(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    return await run_service.list_runs(db, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=TestRunRead)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    # Cancel Celery task if still running
    if run.celery_task_id and run.status in ("pending", "running"):
        try:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(run.celery_task_id, terminate=True)
        except Exception:
            pass

    file_service.cleanup_run_files(run_id)
    await db.delete(run)
    await db.commit()
