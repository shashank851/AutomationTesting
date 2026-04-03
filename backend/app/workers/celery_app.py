from celery import Celery
from app.config import settings

celery_app = Celery("uitest")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,

    # One task at a time per worker process (Playwright is heavyweight)
    worker_prefetch_multiplier=1,
    task_acks_late=True,

    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Task routing
    task_routes={"app.workers.tasks.*": {"queue": "test_runs"}},

    # Hard timeout: kill worker if a task runs too long
    task_time_limit=settings.run_timeout_seconds + 60,
    task_soft_time_limit=settings.run_timeout_seconds,
)

# Auto-discover tasks in the workers package
celery_app.autodiscover_tasks(["app.workers"])
