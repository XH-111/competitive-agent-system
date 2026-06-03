import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def enqueue_kb_ingestion_job(job: Callable[[], None]) -> None:
    """Run KB ingestion in a daemon thread.

    This keeps the LangGraph request path non-blocking and can later be replaced
    by Celery, Redis Queue, or another durable queue.
    """

    def run_job() -> None:
        try:
            job()
        except Exception:  # noqa: BLE001
            logger.exception("kb_ingestion_failed")

    thread = threading.Thread(target=run_job, name="kb-ingestion", daemon=True)
    thread.start()

