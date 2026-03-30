import sys

import django
from django.apps import AppConfig


class TrainerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trainer"

    def ready(self) -> None:
        # Django 4.2 isn't compatible with Python 3.14's handling of copy(super()).
        # Patch BaseContext.__copy__ so the test client can safely inspect template contexts.
        if sys.version_info < (3, 14) or django.VERSION >= (5, 0):
            return

        from django.template.context import BaseContext

        if getattr(BaseContext.__copy__, "_trainer_py314_patch", False):
            return

        def _patched_base_context_copy(self):
            duplicate = self.__class__.__new__(self.__class__)
            duplicate.__dict__.update(self.__dict__)
            duplicate.dicts = self.dicts[:]
            return duplicate

        _patched_base_context_copy._trainer_py314_patch = True
        BaseContext.__copy__ = _patched_base_context_copy
