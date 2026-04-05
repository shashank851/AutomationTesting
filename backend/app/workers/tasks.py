"""
Celery task: execute_test_run
Runs all test cases for a given run_id and persists results to the DB.
Progress events are published to Redis for SSE streaming.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import redis
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SyncSessionLocal
from app.models import TestRun, TestResult, RunEvent
from app.workers.celery_app import celery_app
from runner.executor import TestRunExecutor

logger = logging.getLogger(__name__)


# ── Redis client (shared across task invocations in the same worker process) ──
_redis_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _publish(run_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    """Publish a progress event to the Redis channel for this run."""
    try:
        channel = f"run:{run_id}:events"
        message = json.dumps({"event_type": event_type, "payload": payload})
        _get_redis().publish(channel, message)
    except Exception:
        logger.exception("Failed to publish Redis event for run %s", run_id)


# ── DB helpers (sync, for Celery) ─────────────────────────────────────────────

def _get_run(db: Session, run_id: str) -> Optional[TestRun]:
    return db.query(TestRun).filter(TestRun.id == uuid.UUID(run_id)).first()


def _add_event(db: Session, run_id: str, event_type: str, payload: dict) -> None:
    event = RunEvent(
        run_id=uuid.UUID(run_id),
        event_type=event_type,
        payload=payload,
    )
    db.add(event)
    db.commit()


def _upsert_result(
    db: Session,
    run_id: str,
    *,
    test_case_id: str,
    status: str,
    actual: str,
    comment: str,
    title: Optional[str] = None,
    requires_auth: bool = False,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(TestResult)
        .values(
            run_id=uuid.UUID(run_id),
            test_case_id=test_case_id,
            title=title,
            status=status,
            actual_result=actual,
            comments=comment,
            requires_auth=requires_auth,
            executed_at=datetime.utcnow(),
        )
        .on_conflict_do_update(
            constraint="uq_test_results_run_test",
            set_=dict(
                status=status,
                actual_result=actual,
                comments=comment,
                executed_at=datetime.utcnow(),
            ),
        )
    )
    db.execute(stmt)
    db.commit()


def _update_run_counters(db: Session, run: TestRun) -> None:
    """Recount from test_results table and update the run row."""
    from sqlalchemy import func

    rows = (
        db.query(TestResult.status, func.count(TestResult.id))
        .filter(TestResult.run_id == run.id)
        .group_by(TestResult.status)
        .all()
    )
    counts = {status: cnt for status, cnt in rows}
    run.passed_count  = counts.get("PASS",   0)
    run.failed_count  = counts.get("FAIL",   0)
    run.manual_count  = counts.get("MANUAL", 0)
    run.skipped_count = counts.get("SKIP",   0)
    db.commit()


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.execute_test_run",
    max_retries=0,
)
def execute_test_run(self: Task, run_id: str) -> Dict[str, Any]:
    """
    Main Celery task. Receives the string UUID of a TestRun row,
    executes all test cases, persists results, and marks the run complete.
    """
    logger.info("Starting execute_test_run for run_id=%s", run_id)

    db = SyncSessionLocal()
    try:
        run = _get_run(db, run_id)
        if run is None:
            logger.error("Run %s not found in DB", run_id)
            return {"error": "run not found"}

        # ── Transition to running ─────────────────────────────────────────────
        run.status     = "running"
        run.started_at = datetime.utcnow()
        db.commit()

        _add_event(db, run_id, "status_change", {"status": "running"})
        _publish(run_id, "status_change", {"status": "running"})

        # ── Build progress callback ───────────────────────────────────────────
        def on_progress(event_type: str, payload: Dict[str, Any]) -> None:
            _publish(run_id, event_type, payload)

            if event_type == "test_completed":
                tid     = payload["test_case_id"]
                status  = payload["status"]
                actual  = payload.get("actual", "")
                comment = payload.get("comment", "")
                title   = payload.get("title") or None

                _upsert_result(
                    db, run_id,
                    test_case_id=tid,
                    title=title,
                    status=status,
                    actual=actual,
                    comment=comment,
                )
                # Update counters after each result
                db.refresh(run)
                _update_run_counters(db, run)
                _add_event(db, run_id, "test_completed", payload)

        # ── Execute ───────────────────────────────────────────────────────────
        executor = TestRunExecutor(
            run_id=run_id,
            target_url=run.target_url,
            input_file_path=run.input_file_path,
            output_file_path=run.output_file_path or "",
            auth_state_path=run.auth_state_path,
            timeout_ms=settings.playwright_timeout_ms,
            progress_callback=on_progress,
        )

        try:
            executor.run()
        except SoftTimeLimitExceeded:
            run.status        = "timed_out"
            run.completed_at  = datetime.utcnow()
            run.error_message = "Run exceeded the configured time limit."
            db.commit()
            _add_event(db, run_id, "status_change", {"status": "timed_out"})
            _publish(run_id, "status_change", {"status": "timed_out"})
            return {"status": "timed_out"}

        # ── Mark completed ────────────────────────────────────────────────────
        db.refresh(run)
        _update_run_counters(db, run)
        run.status       = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()

        _add_event(db, run_id, "status_change", {"status": "completed"})
        _publish(run_id, "status_change", {
            "status":        "completed",
            "passed_count":  run.passed_count,
            "failed_count":  run.failed_count,
            "manual_count":  run.manual_count,
            "skipped_count": run.skipped_count,
        })

        logger.info("Run %s completed: P=%d F=%d M=%d S=%d",
                    run_id, run.passed_count, run.failed_count,
                    run.manual_count, run.skipped_count)

        return {"status": "completed", "run_id": run_id}

    except Exception as exc:
        logger.exception("Unhandled error in execute_test_run for run %s", run_id)
        try:
            run = _get_run(db, run_id)
            if run:
                run.status        = "failed"
                run.completed_at  = datetime.utcnow()
                run.error_message = str(exc)[:500]
                db.commit()
                _add_event(db, run_id, "status_change",
                           {"status": "failed", "error": str(exc)[:500]})
                _publish(run_id, "status_change",
                         {"status": "failed", "error": str(exc)[:500]})
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)
        raise

    finally:
        db.close()
