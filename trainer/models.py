from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class LanguageChoices(models.TextChoices):
    ENGLISH = "en", "English"
    SPANISH = "es", "Spanish"
    GERMAN = "de", "German"


class TrainingMode(models.TextChoices):
    MULTIPLE_CHOICE = "multiple_choice", "Multiple Choice"
    FLASHCARDS = "flashcards", "Flashcards"
    SPELLING = "spelling", "Spelling"
    REVIEW = "review", "Review"


class ReminderChannel(models.TextChoices):
    WEBHOOK = "webhook", "Webhook"


class AchievementConditionType(models.TextChoices):
    FIRST_REVIEW = "first_review", "First review"
    STREAK = "streak", "Streak"
    TOTAL_XP = "total_xp", "Total XP"
    LEARNED_WORDS = "learned_words", "Learned words"


class XpSourceType(models.TextChoices):
    MULTIPLE_CHOICE_CORRECT = "multiple_choice_correct", "Multiple choice correct"
    SPELLING_CORRECT = "spelling_correct", "Spelling correct"
    REVIEW_CORRECT = "review_correct", "Review correct"
    DECK_COMPLETION = "deck_completion", "Deck completion"


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    target_language = models.CharField(
        max_length=2,
        choices=LanguageChoices.choices,
        blank=True,
        default="",
    )
    total_xp = models.BigIntegerField(default=0)
    streak_count = models.IntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True)
    reminders_enabled = models.BooleanField(default=True)
    reminder_channel = models.CharField(
        max_length=32,
        choices=ReminderChannel.choices,
        default=ReminderChannel.WEBHOOK,
    )
    reminder_webhook_url = models.URLField(blank=True, default="")

    def __str__(self) -> str:
        return f"Profile for {self.user.username}"

    @property
    def level(self) -> int:
        for level, min_xp in settings.LEVEL_THRESHOLDS:
            if self.total_xp >= min_xp:
                return level
        return 1


class Deck(models.Model):
    title = models.CharField(max_length=255)
    icon = models.CharField(max_length=8, default="📚")
    language = models.CharField(max_length=2, choices=LanguageChoices.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_decks",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("title",)

    def __str__(self) -> str:
        return f"{self.icon} {self.title}"

    @property
    def is_public(self) -> bool:
        return self.owner_id is None


class Word(models.Model):
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="words")
    text = models.CharField(max_length=255)
    translation = models.CharField(max_length=255)
    example_sentence = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("text",)
        constraints = [
            models.UniqueConstraint(fields=("deck", "text"), name="unique_word_in_deck"),
        ]

    def __str__(self) -> str:
        return f"{self.text} -> {self.translation}"


class UserWordProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="word_progress")
    word = models.ForeignKey(Word, on_delete=models.CASCADE, related_name="user_progress")
    correct_answers_count = models.IntegerField(default=0)
    is_learned = models.BooleanField(default=False)
    flashcard_known_count = models.IntegerField(default=0)
    last_review = models.DateTimeField(null=True, blank=True)
    next_review = models.DateTimeField(default=timezone.now)
    interval = models.IntegerField(default=settings.SRS_INITIAL_INTERVAL)
    ease_factor = models.FloatField(default=settings.SRS_DEFAULT_EASE_FACTOR)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "word"), name="unique_progress_for_user_word"),
        ]

    def __str__(self) -> str:
        return f"{self.user.username}: {self.word.text} ({self.correct_answers_count})"


class SessionLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="session_logs")
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="session_logs")
    word = models.ForeignKey(Word, on_delete=models.CASCADE, related_name="session_logs")
    mode = models.CharField(max_length=32, choices=TrainingMode.choices)
    session_id = models.CharField(max_length=64)
    is_correct = models.BooleanField(default=False)
    submitted_answer = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user.username}: {self.word.text} [{self.mode}]"


class Achievement(models.Model):
    code = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    icon_url = models.URLField(blank=True, default="")
    condition_type = models.CharField(max_length=32, choices=AchievementConditionType.choices)
    condition_value = models.IntegerField(default=1)

    class Meta:
        ordering = ("condition_type", "condition_value", "title")

    def __str__(self) -> str:
        return self.title


class UserAchievement(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="earned_achievements")
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE, related_name="user_achievements")
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "achievement"), name="unique_user_achievement"),
        ]
        ordering = ("-unlocked_at",)

    def __str__(self) -> str:
        return f"{self.user.username}: {self.achievement.title}"


class XpEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="xp_events")
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="xp_events", null=True, blank=True)
    word = models.ForeignKey(Word, on_delete=models.CASCADE, related_name="xp_events", null=True, blank=True)
    session_id = models.CharField(max_length=64, blank=True, default="")
    source_type = models.CharField(max_length=64, choices=XpSourceType.choices)
    amount = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user.username}: {self.amount} XP ({self.source_type})"


class NotificationDelivery(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_deliveries")
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=NotificationStatus.choices, default=NotificationStatus.QUEUED)
    attempts = models.IntegerField(default=0)
    response_code = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user.username}: {self.status}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance: User, created: bool, **kwargs) -> None:
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=XpEvent)
def invalidate_leaderboard_cache(sender, **kwargs) -> None:
    cache.delete_many(["leaderboard:weekly", "leaderboard:all_time"])
