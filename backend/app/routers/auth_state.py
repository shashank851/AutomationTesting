"""
POST /api/v1/runs/{run_id}/auth-state        — upload auth_state.json → auto-starts run
GET  /api/v1/runs/{run_id}/auth-setup-script — download pre-configured setup_auth.py
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import file_service, run_service

router = APIRouter(prefix="/api/v1/runs", tags=["auth"])


@router.post("/{run_id}/auth-state", status_code=status.HTTP_200_OK)
async def upload_auth_state(
    run_id: uuid.UUID,
    file: UploadFile = File(..., description="Playwright auth_state.json"),
    db: AsyncSession = Depends(get_db),
):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    if run.status not in ("pending_auth", "pending"):
        raise HTTPException(
            status_code=409,
            detail=f"Auth state can only be uploaded before a run starts (current: {run.status}).",
        )

    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=422, detail="Expected a .json file.")

    auth_state_path = await file_service.save_auth_state(run_id, file)
    await run_service.set_auth_state_path(db, run, auth_state_path)

    # Auto-dispatch the Celery task now that auth state is available
    from app.workers.tasks import execute_test_run
    task = execute_test_run.delay(str(run_id))
    await run_service.set_celery_task_id(db, run, task.id)

    # Transition to pending so the SSE stream connects
    run.status = "pending"
    await db.commit()

    return {"run_id": str(run_id), "status": "pending", "message": "Auth state saved. Run started."}


@router.get("/{run_id}/auth-setup-script", response_class=PlainTextResponse)
async def get_auth_setup_script(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return a ready-to-run setup_auth.py pre-configured with this run's target URL."""
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    script = f'''#!/usr/bin/env python3
"""
Auth setup script — generated for run {run_id}
Target: {run.target_url}

Steps:
  1. pip install playwright
  2. playwright install chromium
  3. python setup_auth_{run_id}.py
  4. Log in when the browser opens, then close the browser window.
  5. Upload the saved auth_state.json back to the platform.
"""
import os, subprocess, sys

TARGET_URL = "{run.target_url}"
AUTH_STATE_FILE = "auth_state.json"

print("\\n" + "=" * 60)
print("  UI TEST PLATFORM — Authentication Setup")
print(f"  Target: {{TARGET_URL}}")
print("=" * 60)
print()
print("  A browser window will open. Log in, then close it.")
print()

result = subprocess.run([
    sys.executable, "-m", "playwright", "open",
    "--save-storage", AUTH_STATE_FILE,
    TARGET_URL
])

if result.returncode != 0 or not os.path.exists(AUTH_STATE_FILE):
    print("\\n  ERROR: Browser closed without saving a session.")
    sys.exit(1)

size = os.path.getsize(AUTH_STATE_FILE)
if size < 500:
    print(f"\\n  ERROR: auth_state.json is too small ({{size}} bytes). Login may not have completed.")
    sys.exit(1)

print(f"\\n  SUCCESS: auth_state.json saved ({{size:,}} bytes)")
print("\\n  Next step: upload auth_state.json to the platform.")
'''

    return PlainTextResponse(
        content=script,
        headers={
            "Content-Disposition": f'attachment; filename="setup_auth_{run_id}.py"'
        },
    )
