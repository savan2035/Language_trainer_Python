from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView

from .forms import CSVImportForm, DeckForm, ProfileForm, RegistrationForm, SpellingForm, WordFormSet
from .models import Deck, Profile, TrainingMode, UserWordProgress, Word
from .services import (
    DECK_BASED_XP_MODES,
    build_answer_options,
    build_language_progress,
    build_public_profile_context,
    create_review_session,
    create_session_log,
    create_training_session,
    get_accessible_decks,
    get_due_review_count,
    get_leaderboard,
    get_recent_mistakes,
    get_training_result_key,
    get_training_session_key,
    import_words_into_deck,
    maybe_award_completion_xp,
    maybe_award_correct_answer_xp,
    normalize_text,
    register_flashcard_feedback,
    seed_default_achievements,
    unlock_earned_achievements,
    update_learning_progress,
    update_streak_for_activity,
)


MODE_LABELS = {
    TrainingMode.MULTIPLE_CHOICE: "Multiple Choice",
    TrainingMode.FLASHCARDS: "Карточки",
    TrainingMode.SPELLING: "Spelling",
    TrainingMode.REVIEW: "Повторение",
}


def home(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")

    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.target_language:
        return redirect("profile")
    return redirect("dashboard")


class RegisterView(FormView):
    template_name = "registration/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("profile")

    def form_valid(self, form: RegistrationForm) -> HttpResponse:
        user = form.save()
        login(self.request, user)
        messages.success(self.request, "Аккаунт создан. Выберите язык и настройте напоминания.")
        return super().form_valid(form)


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "trainer/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("dashboard")

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        kwargs["instance"] = profile
        return kwargs

    def form_valid(self, form: ProfileForm) -> HttpResponse:
        form.save()
        messages.success(self.request, "Профиль сохранен.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["profile"] = self.request.user.profile
        context["level"] = self.request.user.profile.level
        return context


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "trainer/dashboard.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        seed_default_achievements()
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        accessible_decks = get_accessible_decks(self.request.user)
        owned_decks = accessible_decks.filter(owner=self.request.user).annotate(word_count=Count("words"))
        earned_achievements = self.request.user.earned_achievements.select_related("achievement")[:5]

        context.update(
            {
                "profile": profile,
                "learned_total": UserWordProgress.objects.filter(
                    user=self.request.user,
                    is_learned=True,
                ).count(),
                "language_progress": build_language_progress(self.request.user),
                "recent_mistakes": get_recent_mistakes(self.request.user),
                "owned_decks": owned_decks,
                "due_review_count": get_due_review_count(self.request.user),
                "level": profile.level,
                "earned_achievements": earned_achievements,
                "leaderboard_preview": get_leaderboard("weekly")[:5],
            }
        )
        return context


class LeaderboardView(LoginRequiredMixin, TemplateView):
    template_name = "trainer/leaderboard.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        period = self.request.GET.get("period", "all_time")
        if period not in {"all_time", "weekly"}:
            period = "all_time"
        context["period"] = period
        context["rows"] = get_leaderboard(period)
        return context


class PublicProfileView(LoginRequiredMixin, TemplateView):
    template_name = "trainer/public_profile.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        target_user = get_object_or_404(Profile.objects.select_related("user"), user_id=self.kwargs["user_id"]).user
        context.update(build_public_profile_context(target_user))
        return context


class DeckListView(LoginRequiredMixin, TemplateView):
    template_name = "trainer/deck_list.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        if not profile.target_language:
            return context

        decks = (
            get_accessible_decks(self.request.user, language=profile.target_language)
            .annotate(word_count=Count("words"))
            .select_related("owner")
        )
        learned_progress = UserWordProgress.objects.filter(
            user=self.request.user,
            word__deck__in=decks,
            is_learned=True,
        ).values_list("word__deck_id")

        learned_by_deck: dict[int, int] = {}
        for deck_id in learned_progress:
            learned_by_deck[deck_id[0]] = learned_by_deck.get(deck_id[0], 0) + 1

        deck_cards = []
        for deck in decks:
            deck_cards.append(
                {
                    "deck": deck,
                    "word_count": deck.word_count,
                    "learned_count": learned_by_deck.get(deck.id, 0),
                    "is_owner": deck.owner_id == self.request.user.id,
                }
            )

        context["profile"] = profile
        context["deck_cards"] = deck_cards
        context["due_review_count"] = get_due_review_count(self.request.user)
        return context

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        profile, _ = Profile.objects.get_or_create(user=request.user)
        if not profile.target_language:
            messages.info(request, "Сначала выберите язык в профиле.")
            return redirect("profile")
        return super().dispatch(request, *args, **kwargs)


def _get_accessible_deck_or_404(user, deck_id: int) -> Deck:
    profile, _ = Profile.objects.get_or_create(user=user)
    deck = get_object_or_404(
        Deck.objects.prefetch_related("words").select_related("owner").filter(
            Q(owner__isnull=True) | Q(owner=user)
        ),
        pk=deck_id,
    )
    if profile.target_language and deck.language != profile.target_language:
        raise Http404("Deck is unavailable for the selected language.")
    return deck


def _get_owned_deck_or_404(user, deck_id: int) -> Deck:
    return get_object_or_404(Deck.objects.prefetch_related("words"), pk=deck_id, owner=user)


def _validate_mode(mode: str, *, allow_review: bool = False) -> str:
    allowed_modes = set(TrainingMode.values)
    if not allow_review:
        allowed_modes.discard(TrainingMode.REVIEW)
    if mode not in allowed_modes:
        raise Http404("Unknown training mode.")
    return mode


@login_required
def deck_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = DeckForm(request.POST)
        formset = WordFormSet(request.POST, prefix="words")

        if form.is_valid() and formset.is_valid():
            deck = form.save(commit=False)
            deck.owner = request.user
            deck.save()
            formset.instance = deck
            formset.save()
            messages.success(request, "Набор создан.")
            return redirect("deck-edit", deck_id=deck.id)
    else:
        form = DeckForm(initial={"language": getattr(request.user.profile, "target_language", "")})
        formset = WordFormSet(prefix="words")

    return render(
        request,
        "trainer/deck_form.html",
        {
            "form": form,
            "formset": formset,
            "title": "Создать свой набор",
            "submit_label": "Сохранить набор",
            "deck": None,
        },
    )


@login_required
def deck_edit(request: HttpRequest, deck_id: int) -> HttpResponse:
    deck = _get_owned_deck_or_404(request.user, deck_id)

    if request.method == "POST":
        form = DeckForm(request.POST, instance=deck)
        formset = WordFormSet(request.POST, instance=deck, prefix="words")

        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Набор обновлен.")
            return redirect("deck-edit", deck_id=deck.id)
    else:
        form = DeckForm(instance=deck)
        formset = WordFormSet(instance=deck, prefix="words")

    return render(
        request,
        "trainer/deck_form.html",
        {
            "form": form,
            "formset": formset,
            "title": "Редактировать набор",
            "submit_label": "Сохранить изменения",
            "deck": deck,
        },
    )


@login_required
def deck_import_csv(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CSVImportForm(request.POST, request.FILES)
        if form.is_valid():
            deck = import_words_into_deck(
                user=request.user,
                title=form.cleaned_data["title"],
                icon=form.cleaned_data["icon"],
                language=form.cleaned_data["language"],
                rows=form.cleaned_data["parsed_rows"],
            )
            messages.success(request, f"CSV импортирован. Создан набор «{deck.title}».")
            return redirect("deck-edit", deck_id=deck.id)
    else:
        form = CSVImportForm(initial={"language": getattr(request.user.profile, "target_language", "")})

    return render(request, "trainer/deck_import.html", {"form": form})


@login_required
def training_start(request: HttpRequest, deck_id: int, mode: str) -> HttpResponse:
    mode = _validate_mode(mode)
    deck = _get_accessible_deck_or_404(request.user, deck_id)
    force_repeat = request.POST.get("force_repeat") == "1"
    reset_first = request.POST.get("reset_progress") == "1"

    if reset_first:
        UserWordProgress.objects.filter(user=request.user, word__deck=deck).delete()
        force_repeat = True
        messages.success(request, "Прогресс набора сброшен.")

    session_state = create_training_session(request.user, deck, mode=mode, force_repeat=force_repeat)

    if session_state["status"] == "empty":
        messages.warning(request, "В наборе пока нет слов.")
        return redirect("deck-list")

    if session_state["status"] == "all_learned":
        return render(
            request,
            "trainer/all_learned.html",
            {"deck": deck, "mode": mode, "mode_label": MODE_LABELS[mode]},
        )

    session_key = get_training_session_key(deck.id, mode)
    result_key = get_training_result_key(deck.id, mode)
    request.session[session_key] = session_state
    request.session.pop(result_key, None)
    request.session.pop(f"{session_key}_word_id", None)
    request.session.pop(f"{session_key}_options", None)
    return redirect("study-session", deck_id=deck.id, mode=mode)


@login_required
def review_start(request: HttpRequest) -> HttpResponse:
    session_state = create_review_session(request.user)
    if session_state["status"] == "empty":
        messages.info(request, "Сегодня пока нет слов для интервального повторения.")
        return redirect("dashboard")

    session_key = get_training_session_key("review", TrainingMode.REVIEW)
    result_key = get_training_result_key("review", TrainingMode.REVIEW)
    request.session[session_key] = session_state
    request.session.pop(result_key, None)
    request.session.pop(f"{session_key}_word_id", None)
    request.session.pop(f"{session_key}_options", None)
    return redirect("review-session")


@login_required
def training_session(request: HttpRequest, deck_id: int, mode: str) -> HttpResponse:
    mode = _validate_mode(mode)
    deck = _get_accessible_deck_or_404(request.user, deck_id)
    session_key = get_training_session_key(deck.id, mode)
    session_state = request.session.get(session_key)

    if not session_state:
        messages.info(request, "Сначала начните тренировку.")
        return redirect("study-start", deck_id=deck.id, mode=mode)

    current_index = session_state["current_index"]
    if current_index >= len(session_state["question_ids"]):
        return _finalize_training(request, deck, mode, session_state, session_key)

    word = get_object_or_404(Word.objects.select_related("deck"), pk=session_state["question_ids"][current_index], deck=deck)
    return _handle_training_step(request, deck, word, mode, session_state, session_key)


@login_required
def review_session(request: HttpRequest) -> HttpResponse:
    session_key = get_training_session_key("review", TrainingMode.REVIEW)
    session_state = request.session.get(session_key)

    if not session_state:
        messages.info(request, "Сначала запустите режим повторения.")
        return redirect("review-start")

    current_index = session_state["current_index"]
    if current_index >= len(session_state["question_ids"]):
        return _finalize_training(request, None, TrainingMode.REVIEW, session_state, session_key)

    word = get_object_or_404(Word.objects.select_related("deck"), pk=session_state["question_ids"][current_index])
    return _handle_training_step(request, None, word, TrainingMode.REVIEW, session_state, session_key)


def _handle_training_step(
    request: HttpRequest,
    deck: Deck | None,
    word: Word,
    mode: str,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    if mode == TrainingMode.MULTIPLE_CHOICE:
        return _multiple_choice_step(request, deck, word, mode, session_state, session_key)
    if mode == TrainingMode.FLASHCARDS:
        return _flashcards_step(request, deck, word, session_state, session_key)
    if mode == TrainingMode.SPELLING:
        return _spelling_step(request, deck, word, session_state, session_key)
    return _multiple_choice_step(request, deck, word, mode, session_state, session_key)


def _multiple_choice_step(
    request: HttpRequest,
    deck: Deck | None,
    word: Word,
    mode: str,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    options_source_deck = word.deck
    if request.method == "POST":
        selected_option = request.POST.get("selected_option")
        current_word_id = request.session.get(f"{session_key}_word_id")
        current_options = request.session.get(f"{session_key}_options", [])
        allowed_option_ids = {str(option["id"]) for option in current_options}

        if str(word.id) != str(current_word_id) or selected_option not in allowed_option_ids:
            messages.warning(request, "Вопрос обновился. Попробуйте выбрать ответ еще раз.")
            request.session.pop(f"{session_key}_word_id", None)
            request.session.pop(f"{session_key}_options", None)
            return redirect(_session_redirect_name(mode), **_session_redirect_kwargs(deck, mode))

        selected_word = Word.objects.filter(pk=selected_option).first()
        is_correct = selected_option == str(word.id)
        create_session_log(
            user=request.user,
            deck=word.deck,
            word=word,
            mode=mode,
            session_id=session_state["session_id"],
            is_correct=is_correct,
            submitted_answer=selected_word.translation if selected_word else "",
        )
        update_streak_for_activity(request.user)
        update_learning_progress(request.user, word, is_correct=is_correct)
        unlock_earned_achievements(request.user)

        if is_correct:
            session_state["correct_answers"] += 1
            maybe_award_correct_answer_xp(
                user=request.user,
                deck=word.deck,
                word=word,
                mode=mode,
                session_state=session_state,
            )
            unlock_earned_achievements(request.user)
            messages.success(request, "Верно!")
        else:
            messages.error(request, f"Неверно. Правильный ответ: {word.translation}.")

        return _advance_session(request, deck, mode, session_state, session_key)

    options = build_answer_options(options_source_deck, word)
    request.session[f"{session_key}_word_id"] = word.id
    request.session[f"{session_key}_options"] = [{"id": option["id"]} for option in options]
    context = _build_session_context(deck, word, session_state)
    context["options"] = options
    return render(request, "trainer/training_session.html", context)


def _flashcards_step(
    request: HttpRequest,
    deck: Deck | None,
    word: Word,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("flashcard_action")
        if action not in {"known", "unknown"}:
            messages.warning(request, "Выберите один из вариантов ответа.")
            return redirect("study-session", deck_id=deck.id, mode=TrainingMode.FLASHCARDS)

        knew_word = action == "known"
        create_session_log(
            user=request.user,
            deck=word.deck,
            word=word,
            mode=TrainingMode.FLASHCARDS,
            session_id=session_state["session_id"],
            is_correct=knew_word,
            submitted_answer=action,
        )
        update_streak_for_activity(request.user)
        register_flashcard_feedback(request.user, word, knew_word)
        unlock_earned_achievements(request.user)
        if knew_word:
            session_state["known_answers"] += 1

        return _advance_session(request, deck, TrainingMode.FLASHCARDS, session_state, session_key)

    context = _build_session_context(deck, word, session_state)
    return render(request, "trainer/flashcards_session.html", context)


def _spelling_step(
    request: HttpRequest,
    deck: Deck | None,
    word: Word,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    if request.method == "POST":
        form = SpellingForm(request.POST)
        if form.is_valid():
            answer = form.cleaned_data["answer"]
            is_correct = normalize_text(answer) == normalize_text(word.text)
            create_session_log(
                user=request.user,
                deck=word.deck,
                word=word,
                mode=TrainingMode.SPELLING,
                session_id=session_state["session_id"],
                is_correct=is_correct,
                submitted_answer=answer,
            )
            update_streak_for_activity(request.user)
            update_learning_progress(request.user, word, is_correct=is_correct)
            unlock_earned_achievements(request.user)

            if is_correct:
                session_state["correct_answers"] += 1
                maybe_award_correct_answer_xp(
                    user=request.user,
                    deck=word.deck,
                    word=word,
                    mode=TrainingMode.SPELLING,
                    session_state=session_state,
                )
                unlock_earned_achievements(request.user)
                messages.success(request, "Верно!")
            else:
                messages.error(request, f"Неверно. Правильный ответ: {word.text}.")

            return _advance_session(request, deck, TrainingMode.SPELLING, session_state, session_key)
    else:
        form = SpellingForm()

    context = _build_session_context(deck, word, session_state)
    context["form"] = form
    return render(request, "trainer/spelling_session.html", context)


def _build_session_context(deck: Deck | None, word: Word, session_state: dict) -> dict:
    return {
        "deck": deck,
        "word": word,
        "mode": session_state["mode"],
        "mode_label": MODE_LABELS[session_state["mode"]],
        "current_number": session_state["current_index"] + 1,
        "total_questions": session_state["total_questions"],
        "correct_answers": session_state["correct_answers"],
        "known_answers": session_state["known_answers"],
        "xp_gained": session_state.get("xp_gained", 0),
        "source_deck": word.deck,
        "session_title": f"{word.deck.icon} {word.deck.title}" if deck is None else f"{deck.icon} {deck.title}",
    }


def _session_redirect_name(mode: str) -> str:
    return "review-session" if mode == TrainingMode.REVIEW else "study-session"


def _session_redirect_kwargs(deck: Deck | None, mode: str) -> dict:
    if mode == TrainingMode.REVIEW:
        return {}
    return {"deck_id": deck.id, "mode": mode}


def _advance_session(
    request: HttpRequest,
    deck: Deck | None,
    mode: str,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    session_state["current_index"] += 1
    request.session[session_key] = session_state
    request.session.pop(f"{session_key}_word_id", None)
    request.session.pop(f"{session_key}_options", None)

    if session_state["current_index"] >= len(session_state["question_ids"]):
        return _finalize_training(request, deck, mode, session_state, session_key)
    return redirect(_session_redirect_name(mode), **_session_redirect_kwargs(deck, mode))


def _finalize_training(
    request: HttpRequest,
    deck: Deck | None,
    mode: str,
    session_state: dict,
    session_key: str,
) -> HttpResponse:
    if deck is not None and mode in DECK_BASED_XP_MODES:
        maybe_award_completion_xp(user=request.user, deck=deck, session_state=session_state)
        unlock_earned_achievements(request.user)

    result_key = get_training_result_key(deck.id if deck else "review", mode)
    request.session[result_key] = {
        "deck_title": deck.title if deck else "Глобальное повторение",
        "deck_icon": deck.icon if deck else "🔥",
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "correct_answers": session_state["correct_answers"],
        "known_answers": session_state["known_answers"],
        "total_questions": session_state["total_questions"],
        "xp_gained": session_state.get("xp_gained", 0),
        "is_review": deck is None,
    }
    request.session.pop(session_key, None)
    request.session.pop(f"{session_key}_word_id", None)
    request.session.pop(f"{session_key}_options", None)
    if deck is None:
        return redirect("review-result")
    return redirect("study-result", deck_id=deck.id, mode=mode)


@login_required
def training_result(request: HttpRequest, deck_id: int, mode: str) -> HttpResponse:
    mode = _validate_mode(mode)
    deck = _get_accessible_deck_or_404(request.user, deck_id)
    result_key = get_training_result_key(deck.id, mode)
    result = request.session.get(result_key)
    if not result:
        messages.info(request, "Результат пока не сформирован.")
        return redirect("deck-list")
    return render(request, "trainer/training_result.html", {"deck": deck, "result": result})


@login_required
def review_result(request: HttpRequest) -> HttpResponse:
    result_key = get_training_result_key("review", TrainingMode.REVIEW)
    result = request.session.get(result_key)
    if not result:
        messages.info(request, "Результат режима повторения пока не сформирован.")
        return redirect("dashboard")
    return render(request, "trainer/training_result.html", {"deck": None, "result": result})
