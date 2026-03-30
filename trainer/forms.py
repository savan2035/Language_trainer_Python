import csv
from io import StringIO

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from tablib import Dataset

from .models import Deck, Profile, Word


class StyledFormMixin:
    def _apply_styles(self) -> None:
        for field in self.fields.values():
            css_class = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()


class RegistrationForm(StyledFormMixin, UserCreationForm):
    email = forms.EmailField(required=False, label="Email")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")
        labels = {
            "username": "Имя пользователя",
            "password1": "Пароль",
            "password2": "Подтверждение пароля",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(label="Имя пользователя")
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("target_language", "reminders_enabled", "reminder_webhook_url")
        labels = {
            "target_language": "Язык для изучения",
            "reminders_enabled": "Включить напоминания",
            "reminder_webhook_url": "Webhook URL для напоминаний",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
        self.fields["reminder_webhook_url"].required = False
        self.fields["reminder_webhook_url"].help_text = "Опционально: webhook для ежедневных уведомлений о повторении."


class DeckForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Deck
        fields = ("title", "icon", "language")
        labels = {
            "title": "Название набора",
            "icon": "Иконка",
            "language": "Язык",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()


class WordForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Word
        fields = ("text", "translation", "example_sentence")
        labels = {
            "text": "Слово",
            "translation": "Перевод",
            "example_sentence": "Пример",
        }
        widgets = {
            "example_sentence": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()


class BaseWordInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        non_deleted_forms = 0

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            cleaned_data = form.cleaned_data
            if not cleaned_data:
                continue

            if self.can_delete and cleaned_data.get("DELETE"):
                continue

            if cleaned_data.get("text") and cleaned_data.get("translation"):
                non_deleted_forms += 1

        if non_deleted_forms == 0:
            raise ValidationError("Добавьте хотя бы одно слово в набор.")


WordFormSet = inlineformset_factory(
    Deck,
    Word,
    form=WordForm,
    formset=BaseWordInlineFormSet,
    extra=5,
    can_delete=True,
)


class CSVImportForm(StyledFormMixin, forms.Form):
    title = forms.CharField(label="Название нового набора", max_length=255)
    icon = forms.CharField(label="Иконка", max_length=8, initial="📥")
    language = forms.ChoiceField(label="Язык", choices=Deck._meta.get_field("language").choices)
    csv_file = forms.FileField(label="CSV-файл")

    expected_headers = {
        ("word", "translation", "example"),
        ("слово", "перевод", "пример"),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]

        try:
            raw_text = uploaded_file.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV должен быть в кодировке UTF-8.") from exc
        finally:
            uploaded_file.seek(0)

        dataset = Dataset()
        try:
            dataset.load(raw_text, format="csv")
        except Exception as exc:
            raise ValidationError("Не удалось прочитать CSV-файл.") from exc

        rows = []
        csv_reader = csv.reader(StringIO(raw_text))
        for index, row in enumerate(csv_reader):
            values = [value.strip() for value in row]
            if not any(values):
                continue

            if index == 0:
                header_key = tuple(value.casefold() for value in values[:3])
                if header_key in self.expected_headers:
                    continue

            if len(values) < 2:
                raise ValidationError("Каждая строка CSV должна содержать минимум: слово, перевод.")

            rows.append(
                {
                    "text": values[0],
                    "translation": values[1],
                    "example_sentence": values[2] if len(values) > 2 else "",
                }
            )

        if not rows:
            raise ValidationError("CSV не содержит слов для импорта.")

        self.cleaned_data["parsed_rows"] = rows
        return uploaded_file


class SpellingForm(StyledFormMixin, forms.Form):
    answer = forms.CharField(label="Ваш ответ", max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
