from django.core.management.base import BaseCommand

from .seed_demo_data import Command as SeedDemoDataCommand


class Command(SeedDemoDataCommand):
    help = "Seeds the database with public decks for English, German and Spanish."
