"""
Запуск Telegram бота.
"""

import asyncio
import sys
import os
import ssl
from pathlib import Path

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from telegram_bot import main

# SSL контекст для российских API (GigaChat)
def create_ssl_context():
    """Создание SSL контекста для российских сертификатов."""
    ssl_context = ssl.create_default_context()
    
    # Путь к сертификату (если скачан)
    cert_path = Path(__file__).parent / "russian_trusted_root_ca.cer"
    if cert_path.exists():
        ssl_context.load_verify_locations(str(cert_path))
    
    return ssl_context


def detect_provider():
    """Определение провайдера по API base URL."""
    api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    if "gigachat" in api_base.lower() or "sber" in api_base.lower():
        return {
            "name": "GigaChat (Сбер)",
            "model": "GigaChat-Pro",
            "needs_ssl": True
        }
    elif "yandex" in api_base.lower():
        return {
            "name": "YandexGPT",
            "model": "yandexgpt-pro",
            "needs_ssl": False
        }
    elif "tbank" in api_base.lower() or "t-pro" in api_base.lower():
        return {
            "name": "T-Pro (T-Bank)",
            "model": "t-pro",
            "needs_ssl": False
        }
    elif "together" in api_base.lower():
        return {
            "name": "Together AI (Llama 70B)",
            "model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            "needs_ssl": False
        }
    elif "groq" in api_base.lower():
        return {
            "name": "Groq (Llama 70B)",
            "model": "llama-3.1-70b-versatile",
            "needs_ssl": False
        }
    elif "cometapi" in api_base.lower():
        return {
            "name": "CometAPI",
            "model": "gpt-4o-mini",
            "needs_ssl": False
        }
    else:
        return {
            "name": "OpenAI",
            "model": "gpt-4o-mini",
            "needs_ssl": False
        }


if __name__ == "__main__":
    # Проверяем конфигурацию
    api_key = os.getenv("OPENAI_API_KEY")
    gigachat_key = os.getenv("GIGACHAT_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE", "")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    # Если есть GigaChat ключ, используем его
    if gigachat_key and not api_key:
        os.environ["OPENAI_API_KEY"] = gigachat_key
        if not api_base:
            os.environ["OPENAI_API_BASE"] = "https://gigachat.devices.sberbank.ru/api/v1"
        api_key = gigachat_key
    
    if not telegram_token or telegram_token == "your-telegram-bot-token-here":
        print("❌ Ошибка: TELEGRAM_BOT_TOKEN не настроен")
        print("   1. Откройте @BotFather в Telegram")
        print("   2. Создайте бота: /newbot")
        print("   3. Добавьте токен в .env")
        sys.exit(1)
    
    if not api_key or api_key in ["your-api-key-here", "your-gigachat-key"]:
        print("❌ Ошибка: API ключ не настроен")
        print("\n📝 Варианты для РФ:")
        print("   1. GigaChat (Сбер) — БЕСПЛАТНО")
        print("      https://developers.sber.ru/studio/workspaces")
        print("      Добавьте в .env:")
        print("      GIGACHAT_API_KEY=ваш-ключ")
        print("\n   2. YandexGPT — работает в РФ")
        print("      https://console.yandex.cloud")
        print("\n   3. T-Pro (T-Bank) — работает в РФ")
        print("      https://developers.tbank.ru")
        print("\n📖 Подробнее: docs/RUSSIAN_API.md")
        sys.exit(1)
    
    # Определяем провайдера
    provider = detect_provider()
    
    print(f"\n🚀 Провайдер: {provider['name']}")
    print(f"   Модель: {provider['model']}")
    print(f"   API: {os.getenv('OPENAI_API_BASE', 'default')}")
    
    if provider['needs_ssl']:
        print("   ⚠️  GigaChat требует SSL сертификат")
        print("   Если возникнет ошибка SSL — скачайте сертификат:")
        print("   https://gu-st.ru/content/lending/russian_trusted_root_ca.cer")
    
    print()
    
    # Устанавливаем модель по умолчанию
    if "--model" not in sys.argv:
        sys.argv.extend(["--model", provider['model']])
    
    asyncio.run(main())