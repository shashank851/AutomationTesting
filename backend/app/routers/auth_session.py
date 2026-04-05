"""
Auth-session endpoints — headful browser via noVNC.

POST  /api/v1/runs/{run_id}/auth-session          → launch headful browser task
GET   /api/v1/runs/{run_id}/auth-session/stream   → SSE: browser_ready / auth_complete / error
POST  /api/v1/runs/{run_id}/auth-session/complete → capture auth state + dispatch test run
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import run_service
from app.workers.auth_tasks import run_auth_browser
from app.workers.tasks import execute_test_run

router = APIRouter(prefix="/api/v1/runs", tags=["auth-session"])

AUTH_DIR = "/app/auth_states"


def _auth_state_path(run_id: str) -> str:
    os.makedirs(AUTH_DIR, exist_ok=True)
    return os.path.join(AUTH_DIR, f"{run_id}_auth_state.json")


def _event_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_events"


def _cmd_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_commands"


# ── Launch auth browser ───────────────────────────────────────────────────────

@router.post("/{run_id}/auth-session")
async def start_auth_session(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != "pending_auth":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not in pending_auth state (current: {run.status}).",
        )

    auth_path = _auth_state_path(str(run_id))

    task = run_auth_browser.apply_async(
        args=[str(run_id), run.target_url, auth_path],
        queue="test_runs",
    )

    await run_service.set_celery_task_id(db, run, task.id)

    return {"status": "started", "task_id": task.id}


# ── SSE: relay auth events (browser_ready, auth_complete, auth_timeout, auth_error) ──

async def _auth_event_generator(run_id: str):
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    channel = _event_channel(run_id)
    await pubsub.subscribe(channel)

    try:
        yield 'data: {"event_type": "connected"}\n\n'

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            raw = message["data"]
            yield f"data: {raw}\n\n"

            try:
                et = json.loads(raw).get("event_type")
                if et in ("auth_complete", "auth_timeout", "auth_error"):
                    break
            except Exception:
                pass

            await asyncio.sleep(0)
    finally:
        await pubsub.unsubscribe(channel)
        await client.aclose()


@router.get("/{run_id}/auth-session/stream")
async def stream_auth_session(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != "pending_auth":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not in pending_auth state (current: {run.status}).",
        )

    return StreamingResponse(
        _auth_event_generator(str(run_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Complete: save auth state + dispatch test run ─────────────────────────────

@router.post("/{run_id}/auth-session/complete")
async def complete_auth_session(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != "pending_auth":
        raise HTTPException(status_code=409, detail="No active auth session.")

    auth_path = _auth_state_path(str(run_id))

    # Tell the browser task to save the session and finish
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.publish(_cmd_channel(str(run_id)), json.dumps({"type": "complete"}))
    finally:
        await client.aclose()

    # Save auth_state_path to DB and queue the test run
    await run_service.set_auth_state_path(db, run, auth_path)
    await run_service.update_run_status(db, run, "pending")

    task = execute_test_run.apply_async(
        args=[str(run_id)],
        queue="test_runs",
    )
    await run_service.set_celery_task_id(db, run, task.id)

    return {"status": "queued", "task_id": task.id}
