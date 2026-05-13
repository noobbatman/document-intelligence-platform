from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "docintel",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Time limits
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_time_limit=settings.celery_task_time_limit,
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Queues (priority routing)
    task_default_queue="documents.normal",
    task_queues={
        "documents.high":   {"exchange": "documents", "routing_key": "high"},
        "documents.normal": {"exchange": "documents", "routing_key": "normal"},
        "webhooks":         {"exchange": "webhooks",  "routing_key": "webhooks"},
    },
    task_routes={
        "app.workers.tasks.process_document_task": {"queue": "documents.normal"},
        "app.workers.tasks.process_document_high_priority": {"queue": "documents.high"},
        "app.workers.tasks.embed_document_task": {"queue": "documents.normal"},
        "app.workers.tasks.generate_draft_task": {"queue": "documents.high"},
        "app.workers.tasks.extract_preferences_task": {"queue": "documents.normal"},
        "app.workers.tasks.dispatch_webhook_task": {"queue": "webhooks"},
        "app.workers.tasks.batch_process_task": {"queue": "documents.normal"},
    },
    imports=("app.workers.tasks",),
)
