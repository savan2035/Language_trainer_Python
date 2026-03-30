from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Deck,
    LanguageChoices,
    NotificationDelivery,
    NotificationStatus,
    Profile,
    SessionLog,
    TrainingMode,
    UserWordProgress,
    Word,
    XpEvent,
    XpSourceType,
)
from .services import (
    award_xp,
    create_review_session,
    create_training_session,
    enqueue_due_review_reminders,
    get_due_review_count,
    get_leaderboard,
    get_training_session_key,
    register_flashcard_feedback,
    reset_inactive_streaks,
    send_notification_delivery,
    update_learning_progress,
    update_streak_for_activity,
)


class BaseTrainerTestCase(TestCase):
    def create_user(self, username: str, *, target_language: str = "en") -> User:
        user = User.objects.create_user(username=username, password="testpass123")
        user.profile.target_language = target_language
        user.profile.save(update_fields=["target_language"])
        return user

    def create_deck(self, *, title: str, language: str = "en", owner=None, words_count: int = 4) -> tuple[Deck, list[Word]]:
        deck = Deck.objects.create(title=title, icon="📚", language=language, owner=owner)
        words = [
            Word.objects.create(
                deck=deck,
                text=f"{title}-word-{index}",
                translation=f"{title}-translation-{index}",
                example_sentence=f"{title} example {index}",
            )
            for index in range(words_count)
        ]
        return deck, words


class ProfileAndSeedTests(BaseTrainerTestCase):
    def test_profile_created_automatically(self):
        user = User.objects.create_user(username="alice", password="testpass123")
        self.assertTrue(hasattr(user, "profile"))
        self.assertEqual(user.profile.total_xp, 0)
        self.assertEqual(user.profile.streak_count, 0)

    def test_public_decks_available_for_all_languages(self):
        for language_code, _ in LanguageChoices.choices:
            public_count = Deck.objects.filter(owner__isnull=True, language=language_code).count()
            self.assertGreaterEqual(public_count, 3)


class ProgressAndSrsTests(BaseTrainerTestCase):
    def setUp(self):
        self.user = self.create_user("bob")
        self.deck, self.words = self.create_deck(title="Core", owner=self.user, words_count=5)
        self.word = self.words[0]

    def test_correct_and_incorrect_answers_update_srs_fields(self):
        reviewed_at = timezone.now()

        progress = update_learning_progress(self.user, self.word, is_correct=True, reviewed_at=reviewed_at)
        self.assertEqual(progress.correct_answers_count, 1)
        self.assertEqual(progress.interval, 1)
        self.assertAlmostEqual(progress.ease_factor, 2.65)
        self.assertEqual(progress.next_review.date(), (reviewed_at + timedelta(days=1)).date())

        progress = update_learning_progress(
            self.user,
            self.word,
            is_correct=True,
            reviewed_at=reviewed_at + timedelta(days=1),
        )
        self.assertEqual(progress.correct_answers_count, 2)
        self.assertEqual(progress.interval, 3)
        self.assertAlmostEqual(progress.ease_factor, 2.8)

        progress = update_learning_progress(
            self.user,
            self.word,
            is_correct=True,
            reviewed_at=reviewed_at + timedelta(days=4),
        )
        self.assertEqual(progress.correct_answers_count, 3)
        self.assertTrue(progress.is_learned)
        self.assertEqual(progress.interval, 8)
        self.assertAlmostEqual(progress.ease_factor, 2.95)

        progress = update_learning_progress(
            self.user,
            self.word,
            is_correct=False,
            reviewed_at=reviewed_at + timedelta(days=5),
        )
        self.assertEqual(progress.correct_answers_count, 3)
        self.assertTrue(progress.is_learned)
        self.assertEqual(progress.interval, 1)
        self.assertAlmostEqual(progress.ease_factor, 2.75)
        self.assertEqual(progress.next_review.date(), (reviewed_at + timedelta(days=6)).date())

    def test_flashcards_feedback_updates_internal_stats_only(self):
        progress = register_flashcard_feedback(self.user, self.word, knew_word=True)
        self.assertIsNotNone(progress)
        self.assertEqual(progress.flashcard_known_count, 1)
        self.assertEqual(progress.correct_answers_count, 0)
        self.assertEqual(progress.interval, 0)

    def test_training_session_skips_learned_words_when_possible(self):
        UserWordProgress.objects.create(
            user=self.user,
            word=self.words[0],
            correct_answers_count=3,
            is_learned=True,
        )

        session_state = create_training_session(self.user, self.deck, mode=TrainingMode.MULTIPLE_CHOICE)

        self.assertEqual(session_state["status"], "ready")
        self.assertNotIn(self.words[0].id, session_state["question_ids"])

    def test_review_session_contains_only_due_words_from_accessible_decks(self):
        other_deck, other_words = self.create_deck(title="Travel", owner=None, words_count=4)
        future_word = self.words[1]
        due_now = timezone.now()

        UserWordProgress.objects.create(
            user=self.user,
            word=self.words[0],
            correct_answers_count=1,
            next_review=due_now - timedelta(hours=1),
            interval=1,
        )
        UserWordProgress.objects.create(
            user=self.user,
            word=other_words[0],
            correct_answers_count=1,
            next_review=due_now - timedelta(hours=2),
            interval=1,
        )
        UserWordProgress.objects.create(
            user=self.user,
            word=future_word,
            correct_answers_count=1,
            next_review=due_now + timedelta(days=2),
            interval=3,
        )

        session_state = create_review_session(self.user)

        self.assertEqual(session_state["status"], "ready")
        self.assertEqual(session_state["total_questions"], 2)
        self.assertCountEqual(
            session_state["question_ids"],
            [self.words[0].id, other_words[0].id],
        )
        self.assertEqual(get_due_review_count(self.user), 2)


class TrainingViewAndXpTests(BaseTrainerTestCase):
    def setUp(self):
        cache.clear()
        self.user = self.create_user("carol")
        self.client.force_login(self.user)
        self.deck, self.words = self.create_deck(title="Starter", owner=self.user, words_count=4)

    @override_settings(TRAINING_SESSION_SIZE=1)
    def test_multiple_choice_awards_xp_and_completion_bonus(self):
        start_url = reverse("study-start", kwargs={"deck_id": self.deck.id, "mode": "multiple_choice"})
        session_url = reverse("study-session", kwargs={"deck_id": self.deck.id, "mode": "multiple_choice"})
        result_url = reverse("study-result", kwargs={"deck_id": self.deck.id, "mode": "multiple_choice"})

        response = self.client.get(start_url)
        self.assertRedirects(response, session_url)

        self.client.get(session_url)
        session = self.client.session
        session_key = get_training_session_key(self.deck.id, TrainingMode.MULTIPLE_CHOICE)
        current_word_id = session[f"{session_key}_word_id"]

        response = self.client.post(session_url, {"selected_option": str(current_word_id)})
        self.assertRedirects(response, result_url)

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_xp, 55)
        self.assertEqual(
            XpEvent.objects.filter(user=self.user, deck=self.deck).values_list("source_type", flat=True).count(),
            2,
        )

        result_response = self.client.get(result_url)
        self.assertContains(result_response, "55")
        self.assertContains(result_response, "1/1")

        progress = UserWordProgress.objects.get(user=self.user, word_id=current_word_id)
        self.assertEqual(progress.correct_answers_count, 1)
        self.assertTrue(
            SessionLog.objects.filter(
                user=self.user,
                word_id=current_word_id,
                mode=TrainingMode.MULTIPLE_CHOICE,
                is_correct=True,
            ).exists()
        )

    @override_settings(TRAINING_SESSION_SIZE=1)
    def test_second_deck_session_same_day_does_not_award_xp(self):
        start_url = reverse("study-start", kwargs={"deck_id": self.deck.id, "mode": "multiple_choice"})
        session_url = reverse("study-session", kwargs={"deck_id": self.deck.id, "mode": "multiple_choice"})

        self.client.get(start_url)
        self.client.get(session_url)
        first_session = self.client.session
        session_key = get_training_session_key(self.deck.id, TrainingMode.MULTIPLE_CHOICE)
        first_word_id = first_session[f"{session_key}_word_id"]
        self.client.post(session_url, {"selected_option": str(first_word_id)})

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_xp, 55)

        self.client.get(start_url)
        self.client.get(session_url)
        second_session = self.client.session
        second_word_id = second_session[f"{session_key}_word_id"]
        self.client.post(session_url, {"selected_option": str(second_word_id)})

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_xp, 55)

    @override_settings(TRAINING_SESSION_SIZE=1)
    def test_review_page_uses_due_words_from_multiple_decks(self):
        public_deck, public_words = self.create_deck(title="Public", owner=None, words_count=4)
        UserWordProgress.objects.create(
            user=self.user,
            word=self.words[0],
            correct_answers_count=1,
            next_review=timezone.now() - timedelta(minutes=5),
            interval=1,
        )
        UserWordProgress.objects.create(
            user=self.user,
            word=public_words[0],
            correct_answers_count=1,
            next_review=timezone.now() - timedelta(minutes=5),
            interval=1,
        )

        response = self.client.get(reverse("review-start"))
        self.assertRedirects(response, reverse("review-session"))

        session_response = self.client.get(reverse("review-session"))
        self.assertEqual(session_response.status_code, 200)
        self.assertContains(session_response, "Источник:")

    def test_dashboard_shows_due_review_cta_and_recent_mistakes(self):
        due_word = self.words[0]
        UserWordProgress.objects.create(
            user=self.user,
            word=due_word,
            correct_answers_count=1,
            next_review=timezone.now() - timedelta(minutes=10),
            interval=1,
        )
        SessionLog.objects.create(
            user=self.user,
            deck=self.deck,
            word=self.words[1],
            mode=TrainingMode.MULTIPLE_CHOICE,
            session_id="mistake-session",
            is_correct=False,
            submitted_answer="wrong",
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Начать повторение")
        self.assertContains(response, self.words[1].text)
        self.assertContains(response, self.words[1].translation)


class StreakAndLeaderboardTests(BaseTrainerTestCase):
    def setUp(self):
        cache.clear()
        self.user = self.create_user("dora")
        self.other_user = self.create_user("eric")
        self.deck, self.words = self.create_deck(title="Weekly", owner=self.user, words_count=4)

    def test_streak_grows_on_consecutive_days_and_resets_after_gap(self):
        first_day = timezone.now() - timedelta(days=3)
        second_day = timezone.now() - timedelta(days=2)

        update_streak_for_activity(self.user, activity_time=first_day)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.streak_count, 1)

        update_streak_for_activity(self.user, activity_time=second_day)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.streak_count, 2)

        reset_inactive_streaks(today=timezone.localdate())
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.streak_count, 0)

    def test_leaderboard_cache_is_invalidated_by_new_xp_event(self):
        award_xp(
            user=self.user,
            deck=self.deck,
            word=self.words[0],
            session_id="s1",
            source_type=XpSourceType.MULTIPLE_CHOICE_CORRECT,
            amount=100,
        )
        initial_rows = get_leaderboard("all_time")
        self.assertEqual(initial_rows[0]["username"], "dora")

        other_deck, other_words = self.create_deck(title="Other", owner=self.other_user, words_count=4)
        award_xp(
            user=self.other_user,
            deck=other_deck,
            word=other_words[0],
            session_id="s2",
            source_type=XpSourceType.MULTIPLE_CHOICE_CORRECT,
            amount=200,
        )

        updated_rows = get_leaderboard("all_time")
        self.assertEqual(updated_rows[0]["username"], "eric")
        self.assertEqual(updated_rows[0]["xp"], 200)

    def test_leaderboard_and_public_profile_api_require_auth_and_return_data(self):
        award_xp(
            user=self.user,
            deck=self.deck,
            word=self.words[0],
            session_id="weekly-api",
            source_type=XpSourceType.MULTIPLE_CHOICE_CORRECT,
            amount=120,
        )
        update_learning_progress(self.user, self.words[0], is_correct=True)
        update_learning_progress(self.user, self.words[0], is_correct=True)
        update_learning_progress(self.user, self.words[0], is_correct=True)

        anonymous_response = self.client.get(reverse("api-leaderboard"))
        self.assertEqual(anonymous_response.status_code, 403)

        self.client.force_login(self.user)

        leaderboard_response = self.client.get(reverse("api-leaderboard"), {"period": "all_time"})
        self.assertEqual(leaderboard_response.status_code, 200)
        leaderboard_payload = leaderboard_response.json()
        self.assertEqual(leaderboard_payload[0]["username"], "dora")
        self.assertEqual(leaderboard_payload[0]["xp"], 120)

        profile_response = self.client.get(reverse("api-public-profile", kwargs={"user_id": self.user.id}))
        self.assertEqual(profile_response.status_code, 200)
        profile_payload = profile_response.json()
        self.assertEqual(profile_payload["username"], "dora")
        self.assertEqual(profile_payload["learned_words_count"], 1)
        self.assertTrue(profile_payload["learned_languages"])

        review_count_response = self.client.get(reverse("api-review-count"))
        self.assertEqual(review_count_response.status_code, 200)
        self.assertIn("due_review_count", review_count_response.json())


class ReminderAndImportTests(BaseTrainerTestCase):
    def setUp(self):
        cache.clear()
        self.user = self.create_user("frank", target_language="es")
        self.client.force_login(self.user)
        self.deck, self.words = self.create_deck(title="Spanish", language="es", owner=self.user, words_count=4)

    def test_csv_import_creates_private_deck(self):
        csv_content = "word,translation,example\nhola,hello,Hola a todos\nadios,bye,Adios amigo\n"
        uploaded_file = SimpleUploadedFile("test_words.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("deck-import"),
            {
                "title": "Imported Spanish",
                "icon": "📥",
                "language": "es",
                "csv_file": uploaded_file,
            },
        )

        deck = Deck.objects.get(title="Imported Spanish", owner=self.user)
        self.assertRedirects(response, reverse("deck-edit", kwargs={"deck_id": deck.id}))
        self.assertEqual(deck.words.count(), 2)
        self.assertTrue(deck.words.filter(text="hola", example_sentence="Hola a todos").exists())

    def test_enqueue_and_send_webhook_reminders(self):
        self.user.profile.reminder_webhook_url = "https://example.com/reminder"
        self.user.profile.save(update_fields=["reminder_webhook_url"])
        UserWordProgress.objects.create(
            user=self.user,
            word=self.words[0],
            correct_answers_count=1,
            next_review=timezone.now() - timedelta(minutes=15),
            interval=1,
        )

        delivery_ids = enqueue_due_review_reminders()
        self.assertEqual(len(delivery_ids), 1)

        delivery = NotificationDelivery.objects.get(pk=delivery_ids[0])
        self.assertEqual(delivery.status, NotificationStatus.QUEUED)
        self.assertEqual(delivery.payload["due_review_count"], 1)

        mock_response = Mock(status_code=200, text="ok")
        with patch("trainer.services.requests.post", return_value=mock_response) as mocked_post:
            updated_delivery = send_notification_delivery(delivery)

        mocked_post.assert_called_once()
        self.assertEqual(updated_delivery.status, NotificationStatus.SENT)
        self.assertEqual(updated_delivery.response_code, 200)
        self.assertEqual(updated_delivery.attempts, 1)
