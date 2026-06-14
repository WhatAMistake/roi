"""
Полная настройка проекта.
Запускает все этапы конвейера.
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd: str, description: str) -> bool:
    """Запуск команды с выводом."""
    print(f"\n{'='*50}")
    print(f"📌 {description}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            check=True,
            cwd=Path(__file__).parent
        )
        print(f"✅ {description} — успешно")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} — ошибка: {e}")
        return False


def check_env():
    """Проверка .env файла."""
    env_path = Path(__file__).parent / ".env"
    env_example = Path(__file__).parent / ".env.example"
    
    if not env_path.exists():
        print("\n⚠️  Файл .env не найден!")
        print(f"Копирую .env.example → .env")
        
        import shutil
        shutil.copy(env_example, env_path)
        
        print("\n📝 Отредактируйте .env и добавьте ключи:")
        print("   - TELEGRAM_BOT_TOKEN (получить у @BotFather)")
        print("   - OPENAI_API_KEY (получить на together.ai)")
        print("   - OPENAI_API_BASE=https://api.together.xyz/v1")
        return False
    
    # Проверяем ключи
    from dotenv import load_dotenv
    load_dotenv()
    
    import os
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    missing = []
    if not telegram_token or telegram_token == "your-telegram-bot-token-here":
        missing.append("TELEGRAM_BOT_TOKEN")
    if not api_key or api_key == "your-api-key-here":
        missing.append("OPENAI_API_KEY")
    
    if missing:
        print(f"\n⚠️  Не настроены ключи: {', '.join(missing)}")
        print("   Отредактируйте файл .env")
        return False
    
    print(f"\n✅ Конфигурация:")
    print(f"   API Base: {api_base}")
    print(f"   Telegram: {'настроен' if telegram_token else 'не настроен'}")
    
    return True


def check_books():
    """Проверка наличия книг."""
    books_dir = Path(__file__).parent / "books"
    
    if not books_dir.exists():
        books_dir.mkdir()
        print(f"\n📁 Создана папка: {books_dir}")
    
    files = list(books_dir.glob("*.pdf")) + \
            list(books_dir.glob("*.txt")) + \
            list(books_dir.glob("*.docx"))
    
    if not files:
        print(f"\n⚠️  Папка books/ пуста!")
        print("   Добавьте PDF/TXT/DOCX файлы:")
        print("   - Ялом - Экзистенциальная психотерапия.pdf")
        print("   - Франкл - Сказать жизни Да.pdf")
        print("   - ...")
        return False
    
    print(f"\n📚 Найдено книг: {len(files)}")
    for f in files:
        print(f"   - {f.name}")
    
    return True


def main():
    """Основной процесс настройки."""
    print("""
╔═══════════════════════════════════════════════════════════════╗
║          ЭКЗИСТЕНЦИАЛЬНЫЙ ТЕРАПЕВТ-БОТ: НАСТРОЙКА              ║
╚═══════════════════════════════════════════════════════════════╝
""")
    
    # Шаг 0: Проверка окружения
    print("\n[ШАГ 0] Проверка конфигурации")
    if not check_env():
        print("\n❌ Настройте .env и запустите снова")
        return
    
    # Шаг 1: Проверка книг
    print("\n[ШАГ 1] Проверка книг")
    has_books = check_books()
    
    # Шаг 2: Конвертация датасета
    print("\n[ШАГ 2] Конвертация датасета")
    if not run_command(
        "python src/convert_dataset.py",
        "Конвертация xlsx → JSON"
    ):
        print("   Пропускаем (возможно уже сконвертирован)")
    
    # Шаг 3: Индексация книг (если есть)
    if has_books:
        print("\n[ШАГ 3] Индексация книг")
        run_command(
            "python src/index_books.py",
            "Индексация PDF → RAG"
        )
    
    # Итог
    print("""
╔═══════════════════════════════════════════════════════════════╗
║                    НАСТРОЙКА ЗАВЕРШЕНА                        ║
╚═══════════════════════════════════════════════════════════════╝

🚀 Запуск бота:
   python run_telegram.py

📖 Или CLI версия:
   python run.py
""")


if __name__ == "__main__":
    main()