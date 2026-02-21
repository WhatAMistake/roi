# API из России: варианты

## Проблема

OpenAI, Together AI, Groq — заблокированы в РФ, российские карты не принимаются.

---

## Решение: Российские LLM провайдеры

### 1. GigaChat (Сбер) — РЕКОМЕНДУЕТСЯ

| Параметр | Значение |
|----------|----------|
| Цена | **Бесплатно** до 1M токенов/месяц |
| VPN | Не нужен |
| Качество | ~GPT-3.5 |
| API | OpenAI-совместимый |

**Получение ключа:**

1. Перейдите на https://developers.sber.ru/studio/workspaces
2. Авторизуйтесь через Сбер ID или Госуслуги
3. Создайте проект → API ключ
4. Скопируйте ключ

**Настройка .env:**

```env
TELEGRAM_BOT_TOKEN=ваш-токен-бота

# GigaChat
OPENAI_API_KEY=ваш-gigachat-ключ
OPENAI_API_BASE=https://gigachat.devices.sberbank.ru/api/v1
```

**Важно:** GigaChat требует SSL сертификат. При первом запуске скачайте его:

```bash
# Windows (PowerShell)
Invoke-WebRequest -Uri https://gu-st.ru/content/lending/russian_trusted_root_ca.cer -OutFile russian_trusted_root_ca.cer

# Или используйте флаг ignore SSL в коде
```

---

### 2. YandexGPT

| Параметр | Значение |
|----------|----------|
| Цена | ~$0.30/1M tokens |
| VPN | Не нужен |
| Качество | ~GPT-3.5 |

**Получение ключа:**

1. https://console.yandex.cloud
2. Создайте сервисный аккаунт
3. Получите API ключ

**Настройка .env:**

```env
OPENAI_API_KEY=ваш-yandex-ключ
OPENAI_API_BASE=https://llm.api.cloud.yandex.net/foundationModels/v1
```

---

### 3. T-Pro (T-Bank)

| Параметр | Значение |
|----------|----------|
| Цена | ~$0.20/1M tokens |
| VPN | Не нужен |

**Получение ключа:**

1. https://developers.tbank.ru
2. Создайте приложение
3. Получите API ключ

---

## Сравнение

| Провайдер | Цена | Качество | Удобство |
|-----------|------|----------|----------|
| **GigaChat** | Бесплатно | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| YandexGPT | $0.30/1M | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| T-Pro | $0.20/1M | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| Together AI* | $0.88/1M | ⭐⭐⭐⭐⭐ | ⭐⭐ |

*Требует VPN и зарубежную карту

---

## Рекомендация для MVP

**GigaChat (Сбер)** — лучший выбор:
- Бесплатно
- Работает из РФ
- Качество достаточное для тестирования
- После MVP можно перейти на Llama 70B через VPN

---

## Если нужен VPN

Для доступа к Together AI / OpenAI:

1. **Бесплатные:** Proton VPN, Windscribe (ограничены)
2. **Платные:** ExpressVPN, NordVPN, Surfshark
3. **Свои:** VPS + WireGuard / Outline

Оплата: крипта, зарубежные карты, посредники (например, B2BPay)