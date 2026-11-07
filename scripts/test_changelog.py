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
    "Улучшили то, что можно улучшить.",
    "Обновление: шаг к более глубокому диалогу.",
    "Всё течёт, всё меняется.",
]

CHANGELOG_COMMENTS_EN = [
    "We've improved what could be improved.",
    "Update: a step toward deeper dialogue.",
    "Everything flows, everything changes.",
]


EXISTING_COMMANDS = [
    "start", "help", "lang", "switchlang", "reset", "assoc", "lysis",
    "meta", "silence", "void", "meaning", "meaning_is", "meaning_gone",
    "meaning_where", "feedback", "stats", "askprob",
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
        if hash_file.exists():
            with open(hash_file, encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, dict) and "_update_state" in existing:
                raise RuntimeError("Refusing to overwrite durable update state")
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(hash_file, 'w', encoding='utf-8') as f:
            json.dump(hashes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save hashes: {e}")
        raise

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
    # This legacy script sends whole files and does not use the durable flow.
    # Do not spend tokens or overwrite the new cache with misleading results.
    existing = load_stored_hashes(data_dir)
    if "_update_state" in existing:
        print_error("Legacy generator is incompatible with the durable update cache.")
        print_info("Use /updates in the admin chat or scripts/check_code_cache.py.")
        return 1
    
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
        print_info("Do not delete the hash cache; inspect it with scripts/check_code_cache.py")
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
        prompt = f"""Проанализируй изменения и напиши краткий changelog.

Что искать:
- Только РЕАЛЬНЫЕ изменения из кода (не придумывай)
- Исправленные баги
- Улучшения: шифрование, защита от сбоев, стабильность

ПРАВИЛА:
1. Пиши простым языком, без технических деталей
2. Только 1-3 пункта, но лучше 1 точный, чем 3 придуманных
3. Формат: "- Что изменилось"
4. Без вступлений
5. НЕ упоминай функции с "_", библиотеки, технические термины
6. НЕ ПРИДУМЫВАЙ — если не уверен, пропусти пункт
7. НИ В КОЕМ СЛУЧАЕ НЕ упоминай админские команды
8. Примеры: "Исправлена ошибка при сохранении", "Добавлено шифрование данных"
9. Если в коде нет явных изменений — напиши только: "Внутренние улучшения"


Существующие команды: /start, /help, /lang, /switchlang, /reset, /assoc, /lysis, /meta, /silence, /void, /meaning, /meaning_is, /meaning_gone, /meaning_where, /feedback, /stats, /askprob, /recover, /saveall, /dumpall

АДМИНСКИЕ (не упоминать): /recover, /saveall, /dumpall, /admin

Изменённые файлы:

{diff_content}

Напиши changelog на русском."""
    else:
        prompt = f"""Analyze changes and write a brief changelog.

Look for:
- Only REAL changes from code (do not invent)
- Fixed bugs
- Improvements: encryption, crash protection, stability

RULES:
1. Write in plain language, no technical details
2. Only 1-3 items, but 1 accurate is better than 3 invented
3. Format: "- What changed"
4. No introductions
5. DO NOT mention functions starting with "_", libraries, technical terms
6. DO NOT INVENT — if unsure, skip the item
7. NEVER mention admin-only commands
8. Examples: "Fixed save error", "Added data encryption"
9. If no clear changes in code — write only: "Internal improvements"


Existing commands: /start, /help, /lang, /switchlang, /reset, /assoc, /lysis, /meta, /silence, /void, /meaning, /meaning_is, /meaning_gone, /meaning_where, /feedback, /stats, /askprob, /recover, /saveall, /dumpall

ADMIN (never mention): /recover, /saveall, /dumpall, /admin

Changed files:

{diff_content}

Write changelog in English."""


    try:
        if therapist_bot and therapist_bot.client:
            response = therapist_bot.client.chat.completions.create(
                model=therapist_bot.model,
                messages=[
                    {"role": "system", "content": "Write very briefly, in simple words. No technical details, no library names. Easy to understand for regular users." if lang == "en" else "Пиши очень кратко, простыми словами. Без технических деталей, без названий библиотек. Максимально понятно для обычного пользователя."},
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
