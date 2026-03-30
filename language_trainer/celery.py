import os

from celery import Celery
from celery.schedules import crontab


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "language_trainer.settings")

app = Celery("language_trainer")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "reset-inactive-streaks-nightly": {
        "task": "trainer.tasks.reset_inactive_streaks_task",
        "schedule": crontab(hour=0, minute=5),
    },
    "enqueue-review-reminders-daily": {
        "task": "trainer.tasks.enqueue_review_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
}
