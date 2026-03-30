import random
from datetime import timedelta
from uuid import uuid4

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .achievement_data import DEFAULT_ACHIEVEMENTS
from .models import (
    Achievement,
    AchievementConditionType,
    Deck,
    LanguageChoices,
    NotificationDelivery,
    NotificationStatus,
    Profile,
    ReminderChannel,
    SessionLog,
    TrainingMode,
    UserAchievement,
    UserWordProgress,
    Word,
    XpEvent,
    XpSourceType,
)


SCORING_MODES = {
    TrainingMode.MULTIPLE_CHOICE,
    TrainingMode.SPELLING,
    TrainingMode.REVIEW,
}

DECK_BASED_XP_MODES = {
    TrainingMode.MULTIPLE_CHOICE,
    TrainingMode.SPELLING,
}

LEADERBOARD_CACHE_KEYS = {
    "weekly": "leaderboard:weekly",
    "all_time": "leaderboard:all_time",
}


def get_accessible_decks(user, language: str | None = None):
    queryset = Deck.objects.filter(Q(owner__isnull=True) | Q(owner=user))
    if language:
        queryset = queryset.filter(language=language)
    return queryset


def get_training_session_key(scope: str | int, mode: str) -> str:
    return f"training_session_{scope}_{mode}"


def get_training_result_key(scope: str | int, mode: str) -> str:
    return f"training_result_{scope}_{mode}"


def normalize_text(value: str) -> str:
    return value.strip().casefold()


def get_level_for_xp(total_xp: int) -> int:
    for level, min_xp in settings.LEVEL_THRESHOLDS:
        if total_xp >= min_xp:
            return level
    return 1


def seed_default_achievements() -> None:
    for achievement_data in DEFAULT_ACHIEVEMENTS:
        Achievement.objects.update_or_create(
            code=achievement_data["code"],
            defaults=achievement_data,
        )


def get_learned_word_ids(user, deck: Deck) -> set[int]:
    return set(
        UserWordProgress.objects.filter(user=user, word__deck=deck, is_learned=True).values_list(
            "word_id", flat=True
        )
    )


def get_deck_xp_eligible(user, deck: Deck, target_date=None) -> bool:
    target_date = target_date or timezone.localdate()
    return not XpEvent.objects.filter(user=user, deck=deck, created_at__date=target_date).exists()


def get_xp_eligible_deck_ids(user, decks) -> list[int]:
    today = timezone.localdate()
    ineligible_ids = set(
        XpEvent.objects.filter(user=user, deck__in=decks, created_at__date=today).values_list("deck_id", flat=True)
    )
    return [deck.id for deck in decks if deck.id not in ineligible_ids]


def create_training_session(user, deck: Deck, mode: str, force_repeat: bool = False) -> dict[str, object]:
    word_ids = list(deck.words.values_list("id", flat=True))
    if not word_ids:
        return {"status": "empty"}

    learned_ids = get_learned_word_ids(user, deck)
    all_words_learned = len(learned_ids) == len(word_ids)

    if all_words_learned and not force_repeat:
        return {"status": "all_learned"}

    if learned_ids and not all_words_learned and not force_repeat:
        candidate_ids = [word_id for word_id in word_ids if word_id not in learned_ids]
    else:
        candidate_ids = word_ids

    random.shuffle(candidate_ids)
    question_ids = candidate_ids[: min(len(candidate_ids), settings.TRAINING_SESSION_SIZE)]

    return {
        "status": "ready",
        "mode": mode,
        "session_scope": "deck",
        "session_id": str(uuid4()),
        "question_ids": question_ids,
        "current_index": 0,
        "correct_answers": 0,
        "known_answers": 0,
        "total_questions": len(question_ids),
        "xp_enabled": get_deck_xp_eligible(user, deck),
        "completion_xp_enabled": mode in DECK_BASED_XP_MODES and get_deck_xp_eligible(user, deck),
        "xp_gained": 0,
    }


def get_due_review_words(user):
    now = timezone.now()
    decks = get_accessible_decks(user)
    return Word.objects.filter(deck__in=decks, user_progress__user=user, user_progress__next_review__lte=now).select_related(
        "deck"
    )


def get_due_review_count(user) -> int:
    return get_due_review_words(user).count()


def create_review_session(user) -> dict[str, object]:
    due_words = list(get_due_review_words(user))
    if not due_words:
        return {"status": "empty"}

    random.shuffle(due_words)
    eligible_deck_ids = get_xp_eligible_deck_ids(user, [word.deck for word in due_words])
    return {
        "status": "ready",
        "mode": TrainingMode.REVIEW,
        "session_scope": "review",
        "session_id": str(uuid4()),
        "question_ids": [word.id for word in due_words],
        "current_index": 0,
        "correct_answers": 0,
        "known_answers": 0,
        "total_questions": len(due_words),
        "xp_eligible_deck_ids": eligible_deck_ids,
        "xp_gained": 0,
    }


def build_answer_options(deck: Deck, word: Word) -> list[dict[str, object]]:
    distractor_pool = list(Word.objects.filter(deck=deck).exclude(pk=word.pk))

    if len(distractor_pool) < 3:
        same_language_pool = list(
            Word.objects.filter(deck__language=deck.language).exclude(pk=word.pk).exclude(deck=deck)
        )
        distractor_pool.extend(same_language_pool)

    if len(distractor_pool) < 3:
        fallback_pool = list(
            Word.objects.exclude(pk=word.pk).exclude(pk__in=[candidate.pk for candidate in distractor_pool])
        )
        distractor_pool.extend(fallback_pool)

    unique_pool = list({candidate.pk: candidate for candidate in distractor_pool}.values())
    random.shuffle(unique_pool)
    selected_distractors = unique_pool[:3]

    options = [
        {"id": option_word.pk, "text": option_word.translation, "is_correct": option_word.pk == word.pk}
        for option_word in [word, *selected_distractors]
    ]
    random.shuffle(options)
    return options


def create_session_log(
    *,
    user,
    deck: Deck,
    word: Word,
    mode: str,
    session_id: str,
    is_correct: bool,
    submitted_answer: str = "",
) -> SessionLog:
    return SessionLog.objects.create(
        user=user,
        deck=deck,
        word=word,
        mode=mode,
        session_id=session_id,
        is_correct=is_correct,
        submitted_answer=submitted_answer[:255],
    )


def import_words_into_deck(*, user, title: str, icon: str, language: str, rows: list[dict[str, str]]) -> Deck:
    deck = Deck.objects.create(title=title, icon=icon or "📥", language=language, owner=user)

    seen_words: set[str] = set()
    words = []
    for row in rows:
        normalized = normalize_text(row["text"])
        if not normalized or normalized in seen_words:
            continue
        seen_words.add(normalized)
        words.append(
            Word(
                deck=deck,
                text=row["text"].strip(),
                translation=row["translation"].strip(),
                example_sentence=row["example_sentence"].strip(),
            )
        )

    Word.objects.bulk_create(words)
    return deck


def update_streak_for_activity(user, activity_time=None) -> Profile:
    activity_time = activity_time or timezone.now()
    today = timezone.localdate(activity_time)
    yesterday = today - timedelta(days=1)

    profile = user.profile
    if profile.last_activity_date == today:
        return profile

    if profile.last_activity_date == yesterday:
        profile.streak_count += 1
    else:
        profile.streak_count = 1

    profile.last_activity_date = today
    profile.save(update_fields=["streak_count", "last_activity_date"])
    return profile


def _apply_srs_correct(progress: UserWordProgress, reviewed_at) -> None:
    if progress.interval == settings.SRS_INITIAL_INTERVAL:
        progress.interval = settings.SRS_REVIEW_INTERVALS["new_to_first"]
    elif progress.interval == settings.SRS_REVIEW_INTERVALS["new_to_first"]:
        progress.interval = settings.SRS_REVIEW_INTERVALS["first_to_second"]
    else:
        progress.interval = max(1, round(progress.interval * progress.ease_factor))

    progress.ease_factor = min(3.0, progress.ease_factor + 0.15)
    progress.last_review = reviewed_at
    progress.next_review = reviewed_at + timedelta(days=progress.interval)


def _apply_srs_incorrect(progress: UserWordProgress, reviewed_at) -> None:
    progress.interval = settings.SRS_REVIEW_INTERVALS["failed"]
    progress.ease_factor = max(1.3, progress.ease_factor - 0.2)
    progress.last_review = reviewed_at
    progress.next_review = reviewed_at + timedelta(days=progress.interval)


def update_learning_progress(user, word: Word, *, is_correct: bool, reviewed_at=None) -> UserWordProgress:
    reviewed_at = reviewed_at or timezone.now()
    progress, _ = UserWordProgress.objects.get_or_create(user=user, word=word)

    if is_correct:
        progress.correct_answers_count += 1
        progress.is_learned = progress.correct_answers_count >= settings.LEARNED_WORD_THRESHOLD
        _apply_srs_correct(progress, reviewed_at)
    else:
        progress.is_learned = progress.correct_answers_count >= settings.LEARNED_WORD_THRESHOLD
        _apply_srs_incorrect(progress, reviewed_at)

    progress.save(
        update_fields=[
            "correct_answers_count",
            "is_learned",
            "last_review",
            "next_review",
            "interval",
            "ease_factor",
        ]
    )
    return progress


def register_flashcard_feedback(user, word: Word, knew_word: bool) -> UserWordProgress | None:
    if not knew_word:
        return None

    progress, _ = UserWordProgress.objects.get_or_create(user=user, word=word)
    progress.flashcard_known_count += 1
    progress.save(update_fields=["flashcard_known_count"])
    return progress


def award_xp(*, user, deck: Deck | None, word: Word | None, session_id: str, source_type: str, amount: int) -> XpEvent:
    with transaction.atomic():
        event = XpEvent.objects.create(
            user=user,
            deck=deck,
            word=word,
            session_id=session_id,
            source_type=source_type,
            amount=amount,
        )
        Profile.objects.filter(user=user).update(total_xp=F("total_xp") + amount)
    user.profile.refresh_from_db(fields=["total_xp"])
    return event


def maybe_award_correct_answer_xp(*, user, deck: Deck, word: Word, mode: str, session_state: dict) -> int:
    source_mapping = {
        TrainingMode.MULTIPLE_CHOICE: XpSourceType.MULTIPLE_CHOICE_CORRECT,
        TrainingMode.SPELLING: XpSourceType.SPELLING_CORRECT,
        TrainingMode.REVIEW: XpSourceType.REVIEW_CORRECT,
    }
    reward_mapping = {
        TrainingMode.MULTIPLE_CHOICE: settings.XP_REWARDS["multiple_choice_correct"],
        TrainingMode.SPELLING: settings.XP_REWARDS["spelling_correct"],
        TrainingMode.REVIEW: settings.XP_REWARDS["review_correct"],
    }

    if mode == TrainingMode.REVIEW:
        if deck.id not in session_state.get("xp_eligible_deck_ids", []):
            return 0
    elif not session_state.get("xp_enabled", False):
        return 0

    amount = reward_mapping[mode]
    award_xp(
        user=user,
        deck=deck,
        word=word,
        session_id=session_state["session_id"],
        source_type=source_mapping[mode],
        amount=amount,
    )
    session_state["xp_gained"] += amount
    return amount


def maybe_award_completion_xp(*, user, deck: Deck, session_state: dict) -> int:
    if not session_state.get("completion_xp_enabled", False):
        return 0

    amount = settings.XP_REWARDS["deck_completion"]
    award_xp(
        user=user,
        deck=deck,
        word=None,
        session_id=session_state["session_id"],
        source_type=XpSourceType.DECK_COMPLETION,
        amount=amount,
    )
    session_state["xp_gained"] += amount
    session_state["completion_xp_enabled"] = False
    return amount


def get_learned_languages(user) -> list[dict[str, object]]:
    language_rows = (
        UserWordProgress.objects.filter(user=user, is_learned=True)
        .values("word__deck__language")
        .annotate(count=Count("id"))
        .order_by("word__deck__language")
    )
    labels = dict(LanguageChoices.choices)
    return [
        {
            "code": row["word__deck__language"],
            "label": labels[row["word__deck__language"]],
            "count": row["count"],
        }
        for row in language_rows
    ]


def build_language_progress(user) -> list[dict[str, object]]:
    language_progress = []

    for code, label in LanguageChoices.choices:
        decks = get_accessible_decks(user, language=code)
        total_words = Word.objects.filter(deck__in=decks).count()
        learned_words = UserWordProgress.objects.filter(
            user=user,
            word__deck__in=decks,
            is_learned=True,
        ).count()
        percentage = int((learned_words / total_words) * 100) if total_words else 0
        language_progress.append(
            {
                "code": code,
                "label": label,
                "total_words": total_words,
                "learned_words": learned_words,
                "percentage": percentage,
            }
        )

    return language_progress


def get_recent_mistakes(user):
    latest_scored_log = (
        SessionLog.objects.filter(user=user, mode__in=SCORING_MODES)
        .select_related("deck", "word")
        .order_by("-created_at")
        .first()
    )
    if not latest_scored_log:
        return SessionLog.objects.none()

    return (
        SessionLog.objects.filter(
            user=user,
            session_id=latest_scored_log.session_id,
            is_correct=False,
        )
        .select_related("deck", "word")
        .order_by("-created_at")
    )


def unlock_earned_achievements(user) -> list[UserAchievement]:
    achievements = list(Achievement.objects.all())
    unlocked = []

    learned_count = UserWordProgress.objects.filter(user=user, is_learned=True).count()
    has_review = SessionLog.objects.filter(user=user, mode=TrainingMode.REVIEW).exists()

    for achievement in achievements:
        should_unlock = False

        if achievement.condition_type == AchievementConditionType.FIRST_REVIEW:
            should_unlock = has_review
        elif achievement.condition_type == AchievementConditionType.STREAK:
            should_unlock = user.profile.streak_count >= achievement.condition_value
        elif achievement.condition_type == AchievementConditionType.TOTAL_XP:
            should_unlock = user.profile.total_xp >= achievement.condition_value
        elif achievement.condition_type == AchievementConditionType.LEARNED_WORDS:
            should_unlock = learned_count >= achievement.condition_value

        if should_unlock:
            user_achievement, created = UserAchievement.objects.get_or_create(user=user, achievement=achievement)
            if created:
                unlocked.append(user_achievement)

    return unlocked


def get_leaderboard(period: str = "all_time") -> list[dict[str, object]]:
    cache_key = LEADERBOARD_CACHE_KEYS[period]
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if period == "weekly":
        since = timezone.now() - timedelta(days=7)
        users = (
            User.objects.select_related("profile")
            .annotate(period_xp=Coalesce(Sum("xp_events__amount", filter=Q(xp_events__created_at__gte=since)), 0))
            .filter(period_xp__gt=0)
            .order_by("-period_xp", "username")[:100]
        )
        rows = [
            {
                "rank": index + 1,
                "user_id": user.id,
                "username": user.username,
                "xp": user.period_xp,
                "level": get_level_for_xp(user.profile.total_xp),
                "streak_count": user.profile.streak_count,
            }
            for index, user in enumerate(users)
        ]
    else:
        profiles = Profile.objects.select_related("user").order_by("-total_xp", "user__username")[:100]
        rows = [
            {
                "rank": index + 1,
                "user_id": profile.user_id,
                "username": profile.user.username,
                "xp": profile.total_xp,
                "level": profile.level,
                "streak_count": profile.streak_count,
            }
            for index, profile in enumerate(profiles)
        ]

    cache.set(cache_key, rows, settings.LEADERBOARD_CACHE_TIMEOUT)
    return rows


def build_public_profile_context(target_user: User) -> dict[str, object]:
    achievements = (
        UserAchievement.objects.filter(user=target_user).select_related("achievement").order_by("-unlocked_at")
    )
    learned_languages = get_learned_languages(target_user)
    learned_words_count = UserWordProgress.objects.filter(user=target_user, is_learned=True).count()

    return {
        "target_user": target_user,
        "profile": target_user.profile,
        "level": target_user.profile.level,
        "learned_languages": learned_languages,
        "learned_words_count": learned_words_count,
        "earned_achievements": achievements,
    }


def build_review_reminder_payload(user, due_count: int) -> dict:
    return {
        "type": "daily_review_reminder",
        "username": user.username,
        "due_review_count": due_count,
        "message": f"Не забудьте повторить {due_count} слов сегодня, чтобы сохранить ударный режим!",
        "streak_count": user.profile.streak_count,
    }


def create_notification_delivery(user, payload: dict) -> NotificationDelivery:
    return NotificationDelivery.objects.create(user=user, payload=payload)


def send_notification_delivery(delivery: NotificationDelivery) -> NotificationDelivery:
    delivery.attempts += 1
    delivery.save(update_fields=["attempts"])

    webhook_url = delivery.user.profile.reminder_webhook_url
    try:
        response = requests.post(webhook_url, json=delivery.payload, timeout=settings.NOTIFICATION_WEBHOOK_TIMEOUT)
        delivery.response_code = response.status_code
        delivery.sent_at = timezone.now()
        if 200 <= response.status_code < 300:
            delivery.status = NotificationStatus.SENT
            delivery.error_message = ""
        else:
            delivery.status = NotificationStatus.FAILED
            delivery.error_message = response.text[:1000]
    except requests.RequestException as exc:
        delivery.status = NotificationStatus.FAILED
        delivery.error_message = str(exc)
        delivery.sent_at = timezone.now()

    delivery.save(update_fields=["status", "response_code", "error_message", "sent_at", "attempts"])
    return delivery


def enqueue_due_review_reminders() -> list[int]:
    user_ids = []
    users = User.objects.select_related("profile").filter(
        profile__reminders_enabled=True,
        profile__reminder_channel=ReminderChannel.WEBHOOK,
    )
    for user in users:
        if not user.profile.reminder_webhook_url:
            continue
        due_count = get_due_review_count(user)
        if due_count <= 0:
            continue
        payload = build_review_reminder_payload(user, due_count)
        delivery = create_notification_delivery(user, payload)
        user_ids.append(delivery.id)
    return user_ids


def reset_inactive_streaks(today=None) -> int:
    today = today or timezone.localdate()
    threshold = today - timedelta(days=1)
    updated = Profile.objects.filter(last_activity_date__lt=threshold, streak_count__gt=0).update(streak_count=0)
    return updated
