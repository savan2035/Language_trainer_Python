from django.core.management.base import BaseCommand

from trainer.models import Deck, Word
from trainer.services import seed_default_achievements
from trainer.seed_data import PUBLIC_DECKS


class Command(BaseCommand):
    help = "Creates or updates public decks with initial words."

    def handle(self, *args, **options):
        created_decks = 0
        created_words = 0
        seed_default_achievements()

        for deck_data in PUBLIC_DECKS:
            deck, deck_created = Deck.objects.get_or_create(
                title=deck_data["title"],
                language=deck_data["language"],
                owner=None,
                defaults={"icon": deck_data["icon"]},
            )
            if deck_created:
                created_decks += 1
            elif deck.icon != deck_data["icon"]:
                deck.icon = deck_data["icon"]
                deck.save(update_fields=["icon"])

            for text, translation, example_sentence in deck_data["words"]:
                _, word_created = Word.objects.get_or_create(
                    deck=deck,
                    text=text,
                    defaults={
                        "translation": translation,
                        "example_sentence": example_sentence,
                    },
                )
                if word_created:
                    created_words += 1
                else:
                    updates = []
                    word = Word.objects.get(deck=deck, text=text)
                    if word.translation != translation:
                        word.translation = translation
                        updates.append("translation")
                    if word.example_sentence != example_sentence:
                        word.example_sentence = example_sentence
                        updates.append("example_sentence")
                    if updates:
                        word.save(update_fields=updates)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready. New decks: {created_decks}, new words: {created_words}."
            )
        )
