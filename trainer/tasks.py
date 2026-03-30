from celery import shared_task

from .models import NotificationDelivery
from .services import enqueue_due_review_reminders, reset_inactive_streaks, send_notification_delivery


@shared_task
def reset_inactive_streaks_task():
    return reset_inactive_streaks()


@shared_task
def send_review_reminder_webhook(delivery_id: int):
    delivery = NotificationDelivery.objects.get(pk=delivery_id)
    updated_delivery = send_notification_delivery(delivery)
    return updated_delivery.status


@shared_task
def enqueue_review_reminders():
    delivery_ids = enqueue_due_review_reminders()
    for delivery_id in delivery_ids:
        send_review_reminder_webhook.delay(delivery_id)
    return delivery_ids
