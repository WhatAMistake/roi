#!/usr/bin/env python3
"""
Test changelog generation without restarting the bot.
Run from project root: python3 scripts/test_changelog.py [--lang ru|en] [--save-hashes]

Examples:
    python3 scripts/test_changelog.py              # Test in Russian (default)
    python3 scripts/test_changelog.py --lang en    # Test in English
    python3 scripts/test_changelog.py --save-hashes # Test and update cache
"""

import sys
import os
import argparse
import json
import hashlib
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Setup paths
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()
src_dir = project_root / "src"
data_dir = project_root / "data"

# Add src to path for imports
sys.path.insert(0, str(src_dir))

# Change to project root for dotenv
os.chdir(project_root)

# Load dotenv
from dotenv import load_dotenv
load_dotenv()

# ==== INLINE CODE_REVIEWER FUNCTIONS ====
import random

TRACKED_FILES = [
    "src/telegram_bot.py",
    "src/therapist_bot.py", 
    "src/i18n.py",
    "src/lang_utils.py",
]

CHANGELOG_COMMENTS_RU = [
    "Бот обновился — как и всё живое.",
    "Мы улучшили то, что можно улучшить.",
    "Новые возможности для исследования себя.",
    "Обновление: теперь работает ещё лучше.",
    "Время перемен — бот растёт вместе с нами.",
    "Исправлено то, что мешало погружению.",
    "Код эволюционирует — смысл остаётся.",
    "Обновление: шаг к более глубокому диалогу.",
    "Техника служит терапии — мы её подправили.",
    "Всё течёт, всё меняется. Включая этого бота.",
]

CHANGELOG_COMMENTS_EN = [
    "The bot has evolved — as all living things do.",
    "We've improved what could be improved.",
    "New possibilities for self-exploration.",
    "Update: now works even better.",
    "Time for change — the bot grows with us.",
    "Fixed what was getting in the way of immersion.",
    "Code evolves — meaning remains.",
    "Update: a step toward deeper dialogue.",
    "Technology serves therapy — we've tuned it.",
    "Everything flows, everything changes. Including this bot.",
]

EXISTING_COMMANDS = [
    "start", "help", "lang", "switchlang", "reset", "assoc", "analyze",
    "meta", "silence", "void", "meaning", "meaning_is", "meaning_gone",
    "meaning_where", "look", "feedback", "stats", "askprob",
    "recover", "saveall", "dumpall"
]

def get_changelog_comment(num_changes: int, lang: str = "ru") -> str:
    if lang == "ru":
        return random.choice(CHANGELOG_COMMENTS_RU)
    else:
        return random.choice(CHANGELOG_COMMENTS_EN)

def calculate_file_hash(file_path: Path) -> str:
    try:
        with open(file_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return ""

def load_stored_hashes(data_dir: Path) -> Dict[str, str]:
    hash_file = data_dir / "code_hashes.json"
    if hash_file.exists():
        try:
            with open(hash_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_hashes(data_dir: Path, hashes: Dict[str, str]) -> None:
    hash_file = data_dir / "code_hashes.json"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(hash_file, 'w', encoding='utf-8') as f:
            json.dump(hashes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save hashes: {e}")

def get_changed_files(project_root: Path, stored_hashes: Dict[str, str]) -> List[Tuple[str, str, str]]:
    changed = []
    for rel_path in TRACKED_FILES:
        file_path = project_root / rel_path
        if file_path.exists():
            current_hash = calculate_file_hash(file_path)
            old_hash = stored_hashes.get(rel_path, "")
            if current_hash != old_hash:
                changed.append((rel_path, old_hash, current_hash))
    return changed

# ==== UTILITY FUNCTIONS ====
def print_header(text, width=70):
    print("\n" + "=" * width)
    print(f" {text}")
    print("=" * width)

def print_section(text):
    print(f"\n▶ {text}")

def print_success(text):
    print(f"  ✓ {text}")

def print_error(text):
    print(f"  ✗ {text}")

def print_info(text):
    print(f"  • {text}")

def check_env():
    required = ["OPENAI_API_KEY"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        print_error(f"Missing environment variables: {', '.join(missing)}")
        print_info("Make sure .env file exists in project root")
        return False
    return True

# ==== LOAD THERAPIST_BOT ====
def load_therapist_bot():
    spec = importlib.util.spec_from_file_location("therapist_bot", src_dir / "therapist_bot.py")
    tb_module = importlib.util.module_from_spec(spec)
    sys.modules["therapist_bot"] = tb_module
    spec.loader.exec_module(tb_module)
    return tb_module.ExistentialTherapistBot

def test_changelog(lang="ru", save_hashes=False):
    """Test changelog generation."""
    print_header(f"CHANGELOG TEST [{lang.upper()}]")
    
    # Check environment
    print_section("Checking environment...")
    if not check_env():
        return 1
    print_success("Environment OK")
    
    # Load TherapistBot
    print_section("Loading TherapistBot...")
    try:
        ExistentialTherapistBot = load_therapist_bot()
        print_success("ExistentialTherapistBot class loaded")
    except Exception as e:
        print_error(f"Failed to load: {e}")
        return 1
    
    # Create instance
    print_section("Initializing LLM client...")
    try:
        tb = ExistentialTherapistBot(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            api_base=os.getenv("OPENAI_API_BASE"),
            use_rag=False,
        )
        print_success(f"LLM client ready (model: {tb.model})")
    except Exception as e:
        print_error(f"Failed to initialize: {e}")
        return 1
    
    # Check for changes
    print_section("Scanning for code changes...")
    stored_hashes = load_stored_hashes(data_dir)
    changed_files = get_changed_files(project_root, stored_hashes)
    
    if not changed_files:
        print_info("No code changes detected")
        print_info("Next restart will NOT show 'bot updated' message")
        print_info("To force changelog, modify any tracked file or delete data/code_hashes.json")
        return 0
    
    print_success(f"Found {len(changed_files)} changed file(s):")
    for rel_path, old_hash, new_hash in changed_files:
        status = "NEW" if not old_hash else "MODIFIED"
        print_info(f"{rel_path:30} [{status}]")
    
    # Generate changelog
    print_section("Generating changelog with LLM...")
    print_info("This may take a few seconds...")
    
    try:
        changelog = generate_changelog_with_llm(tb, changed_files, project_root, lang)
        
        if changelog:
            print_header("CHANGELOG PREVIEW", width=70)
            print(changelog)
            print("=" * 70)
            
            print_section("Summary")
            print_success("Changelog generated successfully")
            print_info(f"Language: {lang}")
            print_info(f"Changed files: {len(changed_files)}")
            
            if save_hashes:
                print_section("Updating hash cache...")
                current_hashes = {}
                for rel_path in TRACKED_FILES:
                    file_path = project_root / rel_path
                    if file_path.exists():
                        current_hashes[rel_path] = calculate_file_hash(file_path)
                save_hashes(data_dir, current_hashes)
                print_success("Cache updated - next restart won't trigger notifications")
            else:
                print_info("Run with --save-hashes to update cache")
                print_info("Or run: python3 scripts/init_code_cache.py")
        else:
            print_error("No changelog generated (check logs above)")
        
    except Exception as e:
        print_error(f"Error generating changelog: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

def generate_changelog_with_llm(therapist_bot, changed_files, project_root, lang="ru"):
    """Generate user-friendly changelog."""
    if not changed_files:
        return ""
    
    header_comment = get_changelog_comment(len(changed_files), lang)
    
    diff_sections = []
    for rel_path, old_hash, new_hash in changed_files:
        file_path = project_root / rel_path
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            diff_sections.append(f"File: {rel_path}\n{content[:5000]}")
        except Exception as e:
            diff_sections.append(f"File: {rel_path}\n[Error: {e}]")
    
    diff_content = "\n\n".join(diff_sections)
    
    if lang == "ru":
        prompt = f"""Ты — product manager экзистенциального терапевт-бота. 
Пиши changelog для пользователей в тёплом, человечном стиле.

АНАЛИЗИРУЙ КОД и выяви что изменилось для ПОЛЬЗОВАТЕЛЯ:
- Новые команды терапии (функции cmd_*) — как они помогают в работе с собой
- Улучшения существующих команд — что стало лучше в диалоге
- Исправленные баги — что чинили, чтобы не мешало разговору
- Новые возможности — как это поможет в исследовании себя

ПРАВИЛА:
1. Пиши ТОЛЬКО о реальных изменениях в коде ниже
2. НЕ придумывай команды или фичи которых нет
3. Пиши простым языком, как для друга, не для программиста
4. Используй тёплый, эмпатичный тон (в духе Ирвина Ялома)
5. Максимум 4-5 пунктов, только значимое
6. Формат: "- [Краткое название]: что изменилось и зачем это пользователю"
7. Без повторов в начале пунктов, каждый пункт должен быть уникальным
8. НЕ добавляй никаких заключительных фраз типа "Спасибо", "Благодарим", "С уважением"

Изменённые файлы:
{diff_content}

Напиши changelog на русском. Будь конкретным, но сохраняй тёплый, человечный тон."""
    else:
        prompt = f"""You are a product manager for an existential therapist bot.
Write a changelog for users in a warm, human style.

ANALYZE THE CODE and identify what changed for USERS:
- New therapy commands (cmd_* functions) — how they help with self-work
- Improvements to existing commands — what got better in the dialogue
- Fixed bugs — what was repaired to not interrupt the conversation
- New features — how this helps in self-exploration

RULES:
1. Write ONLY about REAL changes in the code below
2. DO NOT invent commands or features that don't exist
3. Write in plain language, like for a friend, not a programmer
4. Use warm, empathetic tone (in the spirit of Irvin Yalom)
5. Maximum 4-5 items, significant changes only
6. Format: "- [Brief name]: what changed and why it matters to users"
7. No repetitive patterns at the start, each item should be unique
8. DO NOT add any closing remarks like "Thanks", "Thank you", "Best regards"

Changed files:
{diff_content}

Write changelog in English. Be specific but keep a warm, human tone."""

    try:
        if therapist_bot and therapist_bot.client:
            response = therapist_bot.client.chat.completions.create(
                model=therapist_bot.model,
                messages=[
                    {"role": "system", "content": "You write brief, honest release notes. Focus on user value. Warm tone." if lang == "en" else "Ты пишешь краткие, честные release notes. Фокус на пользе для пользователя. Тёплый тон."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=400
            )
            changelog = response.choices[0].message.content.strip()
            
            if changelog and len(changelog) > 10 and "Internal" not in changelog:
                return f"{header_comment}\n\n{changelog}"
            else:
                fallback_text = 'Внутренние улучшения стабильности и надёжности.' if lang == 'ru' else 'Internal stability and reliability improvements.'
                return f"{header_comment}\n\n{changelog if changelog else fallback_text}"

    except Exception as e:
        print(f"   LLM generation failed: {e}")
    
    if lang == "ru":
        return f"{header_comment}\n\nВнутренние улучшения стабильности и надёжности."
    else:
        return f"{header_comment}\n\nInternal stability and reliability improvements."

def main():
    parser = argparse.ArgumentParser(
        description="Test changelog generation without restarting the bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/test_changelog.py              # Test in Russian (default)
  python3 scripts/test_changelog.py --lang en    # Test in English
  python3 scripts/test_changelog.py --save-hashes # Test and update cache
        """
    )
    parser.add_argument(
        "--lang", 
        choices=["ru", "en"], 
        default="ru",
        help="Language for changelog (default: ru)"
    )
    parser.add_argument(
        "--save-hashes",
        action="store_true",
        help="Update hash cache after testing (prevents duplicate notifications)"
    )
    
    args = parser.parse_args()
    exit(test_changelog(lang=args.lang, save_hashes=args.save_hashes))

if __name__ == "__main__":
    main()
