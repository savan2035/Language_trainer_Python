from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_demo_data(apps, schema_editor):
    Deck = apps.get_model("trainer", "Deck")
    Word = apps.get_model("trainer", "Word")

    sample_decks = [
        {
            "title": "Top 50 Verbs",
            "icon": "📘",
            "language": "en",
            "words": [
                ("be", "быть"),
                ("have", "иметь"),
                ("do", "делать"),
                ("say", "говорить"),
                ("go", "идти"),
                ("get", "получать"),
                ("make", "создавать"),
                ("know", "знать"),
                ("think", "думать"),
                ("take", "брать"),
            ],
        },
        {
            "title": "Travel Basics",
            "icon": "✈️",
            "language": "es",
            "words": [
                ("aeropuerto", "аэропорт"),
                ("hotel", "отель"),
                ("billete", "билет"),
                ("mapa", "карта"),
                ("maleta", "чемодан"),
                ("playa", "пляж"),
                ("tren", "поезд"),
                ("calle", "улица"),
                ("viaje", "путешествие"),
                ("reserva", "бронь"),
            ],
        },
        {
            "title": "Daily Objects",
            "icon": "🏠",
            "language": "de",
            "words": [
                ("Tisch", "стол"),
                ("Stuhl", "стул"),
                ("Fenster", "окно"),
                ("Lampe", "лампа"),
                ("Buch", "книга"),
                ("Tasse", "чашка"),
                ("Schluessel", "ключ"),
                ("Tuer", "дверь"),
                ("Tasche", "сумка"),
                ("Uhr", "часы"),
            ],
        },
    ]

    for deck_data in sample_decks:
        deck, _ = Deck.objects.get_or_create(
            title=deck_data["title"],
            language=deck_data["language"],
            defaults={"icon": deck_data["icon"]},
        )
        if deck.icon != deck_data["icon"]:
            deck.icon = deck_data["icon"]
            deck.save(update_fields=["icon"])

        for text, translation in deck_data["words"]:
            Word.objects.get_or_create(
                deck=deck,
                text=text,
                defaults={"translation": translation},
            )


def remove_demo_data(apps, schema_editor):
    Deck = apps.get_model("trainer", "Deck")
    Deck.objects.filter(title="Top 50 Verbs", language="en").delete()
    Deck.objects.filter(title="Travel Basics", language="es").delete()
    Deck.objects.filter(title="Daily Objects", language="de").delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Deck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("icon", models.CharField(default="📚", max_length=8)),
                (
                    "language",
                    models.CharField(
                        choices=[("en", "English"), ("es", "Spanish"), ("de", "German")],
                        max_length=2,
                    ),
                ),
            ],
            options={"ordering": ("title",)},
        ),
        migrations.CreateModel(
            name="Profile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "target_language",
                    models.CharField(
                        blank=True,
                        choices=[("en", "English"), ("es", "Spanish"), ("de", "German")],
                        default="",
                        max_length=2,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Word",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.CharField(max_length=255)),
                ("translation", models.CharField(max_length=255)),
                (
                    "deck",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="words",
                        to="trainer.deck",
                    ),
                ),
            ],
            options={"ordering": ("text",)},
        ),
        migrations.CreateModel(
            name="UserWordProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("correct_answers_count", models.IntegerField(default=0)),
                ("is_learned", models.BooleanField(default=False)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="word_progress",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "word",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_progress",
                        to="trainer.word",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="word",
            constraint=models.UniqueConstraint(fields=("deck", "text"), name="unique_word_in_deck"),
        ),
        migrations.AddConstraint(
            model_name="userwordprogress",
            constraint=models.UniqueConstraint(
                fields=("user", "word"),
                name="unique_progress_for_user_word",
            ),
        ),
        migrations.RunPython(seed_demo_data, remove_demo_data),
    ]
