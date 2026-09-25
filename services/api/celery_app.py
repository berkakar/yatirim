import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "yatirim",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.beat_schedule = {
    # Ornek: TEFAS fon verisini gunde bir kez guncelle
    # "tefas-daily-refresh": {
    #     "task": "tasks.refresh_tefas_data",
    #     "schedule": crontab(hour=20, minute=0),
    # },
}
