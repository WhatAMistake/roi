# Настройка Together API (Llama 3.1 70B)

## Почему Together AI?

| Провайдер | Модель | Цена за 1M tokens | Качество |
|-----------|--------|-------------------|----------|
| OpenAI | GPT-4o | $5.00 | ⭐⭐⭐⭐⭐ |
| OpenAI | GPT-4o-mini | $0.15 | ⭐⭐⭐⭐ |
| **Together AI** | **Llama 3.1 70B** | **$0.88** | ⭐⭐⭐⭐⭐ |
| Groq | Llama 3.1 70B | Бесплатно* | ⭐⭐⭐⭐⭐ |

*Groq — бесплатно, но с ограничениями по速率

**Llama 3.1 70B через Together AI** — оптимальный выбор:
- Качество сопоставимо с GPT-4
- Цена в 5 раз дешевле
- OpenAI-совместимый API

---

## Шаг 1: Регистрация

1. Перейдите на https://api.together.xyz
2. Нажмите **"Sign Up"**
3. Зарегистрируйтесь (Google/GitHub/email)

---

## Шаг 2: Получение API ключа

1. После входа перейдите в **Settings** → **API Keys**
2. Нажмите **"Create API Key"**
3. Скопируйте ключ (выглядит как: `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

---

## Шаг 3: Пополнение баланса

1. Перейдите в **Billing**
2. Минимальный депозит: **$1.00**
3. Для тестирования хватит $5-10 на месяц

---

## Шаг 4: Настройка .env

Отредактируйте файл `.env`:

```env
# Telegram Bot Token (получить у @BotFather)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...

# Together API
OPENAI_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_BASE=https://api.together.xyz/v1
```

---

## Шаг 5: Выбор модели

В файле `run_telegram.py` или при запуске:

```bash
# Llama 3.1 70B (рекомендуется)
python run_telegram.py --model meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo

# Llama 3.1 8B (дешевле, но хуже качество)
python run_telegram.py --model meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo

# Qwen 2.5 72B (альтернатива)
python run_telegram.py --model Qwen/Qwen2.5-72B-Instruct-Turbo
```

---

## Доступные модели на Together AI

| Модель | ID для --model | Цена/1M tokens |
|--------|----------------|----------------|
| Llama 3.1 70B | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` | $0.88 |
| Llama 3.1 8B | `meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo` | $0.18 |
| Qwen 2.5 72B | `Qwen/Qwen2.5-72B-Instruct-Turbo` | $0.90 |
| Mixtral 8x7B | `mistralai/Mixtral-8x7B-Instruct-v0.1` | $0.60 |

---

## Проверка

После настройки запустите:

```bash
python run_telegram.py
```

Если всё настроено правильно, увидите:

```
Загрузка модели эмбеддингов...
RAG инициализирован
LLM клиент инициализирован: meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
Telegram бот запущен
```

---

## Оценка стоимости

| Использование | Токенов/день | Стоимость/месяц |
|---------------|--------------|-----------------|
| 10 пользователей | ~50,000 | ~$1.50 |
| 50 пользователей | ~250,000 | ~$7.50 |
| 100 пользователей | ~500,000 | ~$15.00 |

---

## Альтернатива: Groq (бесплатно)

Если хотите бесплатно — используйте Groq:

```env
OPENAI_API_KEY=gsk_xxxxxxxxxxxxx
OPENAI_API_BASE=https://api.groq.com/openai/v1
```

1. Регистрация: https://console.groq.com
2. Получите API ключ
3. Ограничения: 30 запросов/минута

Модель: `llama-3.1-70b-versatile`