# Рой (Roi) — экзистенциальный терапевт в Telegram

Чат-бот для экзистенциальной терапии в традиции **Ирвина Ялома** и **Виктора Франкла**.
Помогает исследовать темы смысла, свободы, одиночества и конечности жизни.

> не замена живому специалисту. Если вам тяжело или вы в кризисе — обратитесь за профессиональной помощью.

## Возможности

- Диалог в духе экзистенциальной терапии (текст, голос, фото)
- RAG по книгам и датасету ассоциаций (Qdrant + sentence-transformers)
- Анализ ассоциаций (`/assoc`) и разбор ситуации (`/analyze`)
- Русский и английский интерфейс
- Экспериментальный снимок на плёнку (`/shoot`)
- Админ-команды, отзывы и т.д.

## Быстрый старт

### 1. Установка

```bash
git clone https://github.com/WhatAMistake/roi.git
cd roi

python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Конфигурация

```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

Минимально заполните в `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
OPENAI_API_KEY=your-api-key
OPENAI_API_BASE=https://api.openai.com/v1
ADMIN_ID=your-telegram-id
```

Бот работает с любым OpenAI-совместимым API (OpenAI, Together, Groq, CometAPI и др.).
Для провайдеров из РФ см. `docs/RUSSIAN_API.md`.

### 3. Данные (опционально, для RAG)

```bash
# xlsx-датасет ассоциаций -> JSON
python src/convert_dataset.py

# PDF/TXT/DOCX из books/ -> чанки для RAG
python src/index_books.py
```

Или одной командой:

```bash
python setup.py
```

### 4. Запуск

```bash
# Telegram-бот
python run_telegram.py

# CLI-версия
python run.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Начать диалог |
| `/help` | Справка |
| `/reset` | Сбросить историю |
| `/assoc` | Анализ ассоциаций (4 данности) |
| `/analyze` | Глубокий разбор ситуации |
| `/shoot` | Снимок внутреннего состояния на плёнку |
| `/switchlang` | Переключить язык (ru/en) |
| `/lang ru` / `/lang en` | Выбрать язык явно |
| `/feedback ...` | Отзыв разработчику |
| `/void` | Взгляд в пустоту |
| `/silence` | Минута молчания |
| `/meaning` | Смысл момента |
| `/remarque` | Замечание по недавнему диалогу |

Также можно просто писать текстом, присылать фото или голосовые.

## Film-frame (`/shoot`)

Экспериментальная фича: бот собирает сцену по вашему состоянию и генерирует изображение.

В `.env`:

```env
FILM_FRAME_ENABLED=true
FILM_FRAME_ALLOWED_USER_IDS=123456789
FILM_FRAME_MODEL=seedream-5-0-pro-260628
FILM_FRAME_PER_USER_DAILY_LIMIT=2
FILM_FRAME_GLOBAL_DAILY_LIMIT=50
```

Подробности — в `.env.example`.

## Структура проекта

```text
roi/
├── app/features/filmframe/   # /shoot
├── books/                    # книги для индексации (PDF не коммитятся)
├── certs/                    # локальные сертификаты (не коммитятся)
├── data/                     # runtime-данные и индексы (не коммитятся)
├── docs/                     # заметки по API
├── prompts/                  # system prompt (ru/en)
├── scripts/                  # служебные скрипты (code cache и др.)
├── src/                      # ядро бота и RAG
│   ├── telegram_bot.py
│   ├── therapist_bot.py
│   ├── rag.py
│   ├── i18n.py
│   ├── lang_utils.py
│   ├── code_reviewer.py
│   ├── convert_dataset.py
│   └── index_books.py
├── tests/                    # тесты
├── .env.example
├── requirements.txt
├── run_telegram.py           # точка входа Telegram
├── run.py                    # точка входа CLI
└── setup.py                  # подготовка данных
```

## Тесты

```bash
python -m pytest tests/
```

## Лицензия

MIT
