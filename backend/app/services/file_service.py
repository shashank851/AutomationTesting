import os
import uuid
import shutil
from pathlib import Path
from typing import Optional

from fastapi import UploadFile, HTTPException
from openpyxl import load_workbook

from app.config import settings


def _run_upload_dir(run_id: uuid.UUID) -> Path:
    return Path(settings.upload_dir) / str(run_id)


def _run_results_dir(run_id: uuid.UUID) -> Path:
    return Path(settings.results_dir) / str(run_id)


async def save_upload(run_id: uuid.UUID, file: UploadFile) -> str:
    """Persist the uploaded Excel file; return the saved absolute path."""
    upload_dir = _run_upload_dir(run_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / file.filename
    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.max_upload_size_bytes // (1024 * 1024)} MB",
        )

    dest.write_bytes(content)
    return str(dest)


async def save_auth_state(run_id: uuid.UUID, file: UploadFile) -> str:
    """Persist an auth-state JSON file; return the saved absolute path."""
    upload_dir = _run_upload_dir(run_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / "auth_state.json"
    content = await file.read()

    if len(content) < 500:
        raise HTTPException(
            status_code=422,
            detail="auth_state.json appears invalid (< 500 bytes). Re-run setup_auth.py.",
        )

    dest.write_bytes(content)
    return str(dest)


def validate_excel(path: str) -> int:
    """Open the workbook and count test-case rows. Raises HTTPException on invalid file."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot open Excel file: {exc}")

    # Expect first sheet; header on row 1, data from row 2
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    wb.close()

    # Filter blank rows
    data_rows = [r for r in rows if any(c is not None for c in r)]
    count = len(data_rows)

    if count == 0:
        raise HTTPException(status_code=422, detail="Excel file contains no test-case rows.")
    if count > settings.max_test_cases:
        raise HTTPException(
            status_code=422,
            detail=f"Excel file contains {count} rows; maximum is {settings.max_test_cases}.",
        )

    return count


def results_path(run_id: uuid.UUID, input_filename: str) -> str:
    """Return the output Excel path for a completed run."""
    results_dir = _run_results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(input_filename).stem
    return str(results_dir / f"{stem}_results.xlsx")


def cleanup_run_files(run_id: uuid.UUID) -> None:
    """Remove all upload and result files for a run (called on delete)."""
    for base in (_run_upload_dir(run_id), _run_results_dir(run_id)):
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
