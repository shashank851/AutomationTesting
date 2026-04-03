"""
GET /api/v1/runs/{run_id}/stream — Server-Sent Events for real-time progress.

The client connects; the server subscribes to the Redis pub/sub channel
`run:{run_id}:events` and forwards each message as an SSE event.
The stream closes automatically when a terminal status is received
(completed | failed | timed_out | cancelled).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import run_service

router = APIRouter(prefix="/api/v1/runs", tags=["stream"])

TERMINAL_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


async def _event_generator(run_id: str):
    """Async generator that yields SSE-formatted strings."""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    channel = f"run:{run_id}:events"

    await pubsub.subscribe(channel)
    try:
        # Initial keep-alive
        yield "data: {\"event_type\": \"connected\"}\n\n"

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            raw = message["data"]
            yield f"data: {raw}\n\n"

            # Check for terminal status
            try:
                parsed = json.loads(raw)
                if (parsed.get("event_type") == "status_change"
                        and parsed.get("payload", {}).get("status") in TERMINAL_STATUSES):
                    break
            except Exception:
                pass

            # Small yield to prevent tight loops
            await asyncio.sleep(0)

    finally:
        await pubsub.unsubscribe(channel)
        await client.aclose()


@router.get("/{run_id}/stream")
async def stream_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    # Reject if already terminal or still waiting for auth
    if run.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Run already in terminal state: {run.status}",
        )
    if run.status == "pending_auth":
        raise HTTPException(
            status_code=409,
            detail="Run is waiting for auth_state.json to be uploaded.",
        )
    # pending is valid — task is queued but may not have started yet; stream will wait

    return StreamingResponse(
        _event_generator(str(run_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
