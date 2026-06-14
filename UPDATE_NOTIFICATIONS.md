# Update Notifications for Existential Therapist Bot

This feature automatically detects code changes and sends notifications to the admin on bot startup.

## How it works

1. On startup, the bot checks if any tracked source files have changed
2. If changes are detected, it generates a changelog using LLM
3. The changelog is sent to the admin (configured via `ADMIN_ID` in .env)
4. File hashes are stored in `data/code_hashes.json`

## Setup

1. **Initialize the cache** (run once before first deployment):
   ```bash
   cd existential-therapist-bot
   python scripts/init_code_cache.py
   ```

2. **Set admin ID** in `.env`:
   ```
   ADMIN_ID=282208693
   ```

3. **Deploy** - the bot will automatically check for updates on startup

## Testing

Test the changelog generation without running the bot:
```bash
python scripts/test_changelog.py
```

## Tracked files

The following files are monitored for changes:
- `src/telegram_bot.py`
- `src/therapist_bot.py`
- `src/i18n.py`
- `src/lang_utils.py`

## Changelog format

The generated changelog includes:
- Bullet points describing what changed (functional changes, not implementation details)
- A witty, existential-themed comment at the end

Example:
```
📝 Обновление кода

- Добавлена команда /silence для минуты тишины
- Улучшена обработка голосовых сообщений
- Исправлена ошибка при анализе изображений

Код обновлён. Смысл жизни пока остаётся прежним.
```

## Manual reset

If you need to reset the cache (e.g., after major refactoring):
```bash
rm data/code_hashes.json
python scripts/init_code_cache.py
