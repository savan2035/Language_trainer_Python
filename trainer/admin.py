from django.contrib import admin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import (
    Achievement,
    Deck,
    NotificationDelivery,
    Profile,
    SessionLog,
    UserAchievement,
    UserWordProgress,
    Word,
    XpEvent,
)


class DeckResource(resources.ModelResource):
    class Meta:
        model = Deck
        fields = ("id", "title", "icon", "language", "owner")


class WordResource(resources.ModelResource):
    class Meta:
        model = Word
        fields = ("id", "deck", "text", "translation", "example_sentence")


class WordInline(admin.TabularInline):
    model = Word
    extra = 1


@admin.register(Deck)
class DeckAdmin(ImportExportModelAdmin):
    resource_classes = [DeckResource]
    list_display = ("icon", "title", "language", "owner", "visibility", "word_total")
    list_filter = ("language", "owner")
    search_fields = ("title", "owner__username")
    inlines = [WordInline]

    def visibility(self, obj: Deck) -> str:
        return "Public" if obj.is_public else "Private"

    def word_total(self, obj: Deck) -> int:
        return obj.words.count()


@admin.register(Word)
class WordAdmin(ImportExportModelAdmin):
    resource_classes = [WordResource]
    list_display = ("text", "translation", "deck", "short_example")
    list_filter = ("deck__language", "deck")
    search_fields = ("text", "translation", "example_sentence")

    def short_example(self, obj: Word) -> str:
        return obj.example_sentence[:50]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "target_language", "total_xp", "streak_count", "reminders_enabled")
    list_filter = ("target_language", "reminders_enabled")
    search_fields = ("user__username",)


@admin.register(UserWordProgress)
class UserWordProgressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "word",
        "correct_answers_count",
        "flashcard_known_count",
        "interval",
        "ease_factor",
        "next_review",
        "is_learned",
    )
    list_filter = ("is_learned", "word__deck__language")
    search_fields = ("user__username", "word__text", "word__translation")


@admin.register(SessionLog)
class SessionLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "deck", "word", "mode", "is_correct")
    list_filter = ("mode", "is_correct", "deck__language")
    search_fields = ("user__username", "word__text", "word__translation", "submitted_answer")


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "condition_type", "condition_value")
    list_filter = ("condition_type",)
    search_fields = ("code", "title")


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ("user", "achievement", "unlocked_at")
    list_filter = ("achievement",)
    search_fields = ("user__username", "achievement__title")


@admin.register(XpEvent)
class XpEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "deck", "word", "source_type", "amount")
    list_filter = ("source_type",)
    search_fields = ("user__username", "deck__title", "word__text")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "status", "attempts", "response_code", "sent_at")
    list_filter = ("status",)
    search_fields = ("user__username", "error_message")
