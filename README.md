# Языковой тренажер

Django-приложение для изучения слов с несколькими режимами тренировки, SRS-повторением, XP, стриками, лидербордом и webhook-напоминаниями.

## Что уже реализовано

- регистрация, вход и выход на встроенной модели `User`
- профиль пользователя с выбором языка и настройками напоминаний
- публичные и личные наборы слов
- импорт пользовательских наборов из CSV в UTF-8
- режимы `Multiple Choice`, `Flashcards`, `Spelling`
- Stage 3 SRS-поля: `last_review`, `next_review`, `interval`, `ease_factor`
- глобальный режим `Review` для всех слов, готовых к повторению
- XP, уровни, стрики, достижения
- публичные профили пользователей и лидерборд
- DRF API для лидерборда, публичного профиля и числа due-слов
- Celery-задачи для сброса неактивных стриков и отправки webhook-напоминаний

## Стек

- Python 3.10+
- Django 4.2+
- Django REST Framework
- Celery
- Redis
- SQLite по умолчанию

## Быстрый старт

1. Создайте виртуальное окружение:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Установите зависимости:

```powershell
pip install -r requirements.txt
```

3. Примените миграции:

```powershell
python manage.py migrate
```

4. При необходимости повторно загрузите демо-контент:

```powershell
python manage.py seed_demo_data
```

5. Создайте администратора:

```powershell
python manage.py createsuperuser
```

6. Запустите веб-приложение:

```powershell
python manage.py runserver
```

## Redis и Celery

Если `REDIS_URL` не задан, кэш работает через `LocMemCache`, а приложение остается работоспособным локально. Для Stage 3 в полноценном режиме задайте:

```powershell
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
```

Запуск worker:

```powershell
celery -A language_trainer worker -l info
```

Запуск beat:

```powershell
celery -A language_trainer beat -l info
```

По расписанию выполняются:

- ежедневный сброс стриков для давно неактивных пользователей
- ежедневная постановка webhook-напоминаний пользователям, у которых есть due-слова

## API

Все API-эндпоинты работают через session auth и требуют авторизации.

- `GET /api/leaderboard/?period=weekly|all_time`
- `GET /api/profile/<id>/`
- `GET /api/training/review-count/`

## Правила Stage 3

- слово считается выученным после `3` правильных ответов
- `Flashcards` не дают XP и не двигают SRS
- `Multiple Choice` дает `+5 XP` за верный ответ
- `Spelling` дает `+10 XP` за верный ответ
- `Review` дает `+5 XP` за верный ответ
- завершение deck-сессии дает `+50 XP`
- повторное прохождение того же набора в тот же день не дает XP

## Тесты

```powershell
python manage.py test
```

Покрытие тестами включает:

- SRS-обновление прогресса
- review queue
- XP и анти-фарм
- streak reset
- leaderboard и API
- webhook reminders
- CSV import

