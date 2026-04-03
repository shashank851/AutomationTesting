"""
GET  /api/v1/runs/{run_id}/results   — list test results for a run
GET  /api/v1/runs/{run_id}/download  — download the results Excel file
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import TestResultRead
from app.services import run_service

router = APIRouter(prefix="/api/v1/runs", tags=["results"])


@router.get("/{run_id}/results", response_model=List[TestResultRead])
async def get_results(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run.results


@router.get("/{run_id}/download")
async def download_results(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    if run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not completed (current status: {run.status}).",
        )

    if not run.output_file_path:
        raise HTTPException(status_code=404, detail="Output file path not set.")

    path = Path(run.output_file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Results file not found on disk.")

    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )
