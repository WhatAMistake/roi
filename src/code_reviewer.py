"""
Code change reviewer for Existential Therapist bot.
Tracks file hashes and generates changelogs on startup.
"""

import os
import json
import hashlib
import re
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Files to track for changes
TRACKED_FILES = [
    "src/telegram_bot.py",
    "src/therapist_bot.py",
    "src/i18n.py",
    "src/rag.py",
    "src/lang_utils.py",
]

# Existing commands that should NOT be mentioned as new in changelogs
EXISTING_COMMANDS = [
    "start", "help", "lang", "switchlang", "reset", "assoc", "analyze",
    "meta", "void", "silence", "meaning", "feedback", "meaning_where",
    "askprob"
]


# Witty comments for changelog - localized
WITTY_COMMENTS_RU = [
    "Всё течёт, всё меняется. Код тоже.",
    "Как сказал бы Гераклит: код обновлён.",
    "Код преображается, как и мы с вами.",
    "Панта Рей — и код тоже.",
    "Время лечит, а обновления улучшают.",
    "Сизифов труд продолжается. Код поднят на новый уровень.",
    "Как у Ялома: здесь и сейчас — новый код.",
    "Трансформация кода, трансформация опыта.",
    "Обновление: попытка приблизиться к идеалу.",
    "Код меняется. Принять это — первый шаг.",
]

WITTY_COMMENTS_EN = [
    "Everything flows, everything changes. So does the code.",
    "A new version — new opportunities for growth.",
    "Changes that cannot be avoided. And need not be.",
    "Code transforms, as do we.",
    "Update: a step into the unknown with a compass.",
    "Panta Rhei — and so does the code.",
    "Time heals, updates improve.",
    "The Sisyphean labor continues. Code elevated.",
    "Existential freedom of code realized.",
    "Code updated. Meaning preserved.",
    "As Yalom would say: here and now — new code.",
    "Code transformation, experience transformation.",
    "Update: an attempt to approach the ideal.",
    "Code changes. Accepting this is the first step.",
]






def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of file contents."""
    try:
        with open(file_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return ""


def load_stored_hashes(data_dir: Path) -> Dict[str, str]:
    """Load previously stored file hashes."""
    hash_file = data_dir / "code_hashes.json"
    if hash_file.exists():
        try:
            with open(hash_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_hashes(data_dir: Path, hashes: Dict[str, str]) -> None:
    """Save current file hashes."""
    hash_file = data_dir / "code_hashes.json"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(hash_file, 'w', encoding='utf-8') as f:
            json.dump(hashes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save hashes: {e}")


def get_changed_files(project_root: Path, stored_hashes: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """
    Compare current files with stored hashes.
    Returns list of (filename, old_hash, new_hash) for changed files.
    """
    changed = []
    for rel_path in TRACKED_FILES:
        file_path = project_root / rel_path
        if file_path.exists():
            current_hash = calculate_file_hash(file_path)
            old_hash = stored_hashes.get(rel_path, "")
            if current_hash != old_hash:
                changed.append((rel_path, old_hash, current_hash))
    return changed


def extract_commands_from_code(content: str) -> List[str]:
    """Extract command names from code content."""
    commands = []
    patterns = [
        r'Command\(["\'](/\w+)["\']',
        r'@self\.dp\.message\(Command\(["\'](\w+)["\']',
        r'async def cmd_(\w+)\(',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            cmd = match if match.startswith('/') else f"/{match}"
            if cmd not in commands:
                commands.append(cmd)
    return commands


def analyze_code_changes(project_root: Path, changed_files: List[Tuple[str, str, str]]) -> Dict[str, List[str]]:
    """Analyze what actually changed in the code."""
    result = {
        "new_commands": [],
        "all_commands": [],
        "has_changes": False,
        "changed_files_count": len(changed_files)
    }
    
    for rel_path, old_hash, new_hash in changed_files:
        file_path = project_root / rel_path
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            commands = extract_commands_from_code(content)
            if commands:
                result["all_commands"].extend(commands)
            
            result["has_changes"] = True
        except Exception:
            result["has_changes"] = True
    
    # Remove duplicates
    result["all_commands"] = list(set(result["all_commands"]))
    return result


def get_witty_comment(num_changes: int, lang: str = "ru") -> str:
    """Get a witty comment based on language."""
    if lang == "ru":
        return random.choice(WITTY_COMMENTS_RU)
    else:
        return random.choice(WITTY_COMMENTS_EN)





def generate_changelog_with_llm(therapist_bot, changed_files: List[Tuple[str, str, str]], project_root: Path, lang: str = "ru") -> str:
    """
    Use LLM to generate a human-readable changelog based on file changes.
    """
    if not changed_files:
        return ""
    
    num_changes = len(changed_files)
    
    # Generate content for each changed file
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
    
    # Known existing commands to prevent hallucination
    known_cmds = ", ".join(EXISTING_COMMANDS[:12])
    
    if lang == "ru":
        prompt = f"""Ты пишешь changelog для пользователей.
Пиши ТОЛЬКО о том, что изменилось для ПОЛЬЗОВАТЕЛЯ (новые команды, исправления в ответах, новые темы, исправление багов).
Игнорируй чисто техническую чепуху: структуру папок, импорты, мелкий рефакторинг.

СТРОГИЕ ПРАВИЛА:
1) Если есть исправления в логике команд — ОБЯЗАТЕЛЬНО напиши об этом.
2) Пиши максимально кратко: 1-3 пункта.
3) НИКАКИХ "внутренних улучшений" и "оптимизаций".

УЖЕ СУЩЕСТВУЮЩИЕ: {known_cmds}...

Изменённые файлы ({num_changes} шт.):

{diff_content}

Напиши changelog на русском. Будь конкретным и добрым."""


    else:
        prompt = f"""You are writing release notes for users.
Write ONLY about changes that are VISIBLE to the user (new commands, fixed responses, new themes).
Ignore technical fluff: folder structure, refactoring, imports, etc.

RULES:
1. If there are no user-facing changes — return an empty string.
2. Be extremely brief: 1-2 items max.
3. NO "internal improvements" or "under the hood" talk.
4. If a command was fixed — say so.

Changed files:
{diff_content}

Write changelog in English. Be specific and kind."""





    
    try:
        if therapist_bot and therapist_bot.client:
            response = therapist_bot.client.chat.completions.create(
                model=therapist_bot.model,
                messages=[
                    {"role": "system", "content": "Ты пишешь краткие notes к релизам. Не придумывай. Будь честным. НЕ используй markdown (звёздочки, жирный шрифт)." if lang == "ru" else "You write brief release notes. Don't hallucinate. Be honest. NO markdown formatting (no asterisks, no bold)."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,

                max_tokens=800
            )
            changelog = response.choices[0].message.content.strip()
            
            # Add witty comment if changelog is meaningful
            if changelog and len(changelog) > 10 and "Internal" not in changelog:
                witty = get_witty_comment(num_changes, lang)
                return f"{witty}\n\n{changelog}"
            else:
                return changelog


    except Exception as e:
        print(f"LLM changelog generation failed: {e}")
    
    # Fallback: simple message
    if lang == "ru":
        return "Внутренние улучшения и исправления."
    else:
        return "Internal improvements and fixes."


def generate_user_friendly_fallback(analysis: Dict, lang: str = "ru") -> str:
    """Generate a user-friendly changelog without technical details."""
    num_changes = analysis.get("changed_files_count", 1)
    
    if lang == "ru":
        witty = get_witty_comment(num_changes, lang)
        lines = [witty]
        
        if analysis["all_commands"]:
            for cmd in analysis["all_commands"][:5]:
                lines.append(f"- Доступна команда {cmd}")
        
        if len(lines) == 1:
            lines.append("- Улучшена работа и исправлены ошибки")
    else:
        witty = get_witty_comment(num_changes, lang)
        lines = [witty]
        
        if analysis["all_commands"]:
            for cmd in analysis["all_commands"][:5]:
                lines.append(f"- Command {cmd} is available")
        
        if len(lines) == 1:
            lines.append("- Improvements and bug fixes")



    
    return "\n".join(lines)


def check_and_generate_changelog(project_root: Path, therapist_bot, admin_id: int, lang: str = "ru", should_save_hashes: bool = True) -> Optional[str]:
    """
    Main entry point: check for changes and generate changelog.
    Returns changelog text if changes detected, None otherwise.
    
    CRITICAL: Hashes are saved ONLY after successful notifications to prevent 
    duplicate alerts on bot restarts. Use should_save_hashes=False when 
    generating changelogs, then save hashes manually after notifications are sent.
    
    Args:
        project_root: Path to project root
        therapist_bot: TherapistBot instance for LLM calls
        admin_id: Admin user ID (for reference, not used in this function)
        lang: Language code (ru/en)
        should_save_hashes: If True, saves hashes after generating changelog. 
                           Set to False when testing or when you want to save hashes 
                           manually after confirming notifications were sent.
    """

    try:
        data_dir = project_root / "data"
        stored_hashes = load_stored_hashes(data_dir)
        changed_files = get_changed_files(project_root, stored_hashes)
        
        print(f"[CHANGELOG] Stored hashes: {stored_hashes}")
        print(f"[CHANGELOG] Changed files: {changed_files}")
        
        if not changed_files:
            print(f"[CHANGELOG] No changes detected")
            return None
        
        print(f"[CHANGELOG] Generating changelog for {len(changed_files)} changed files...")
        changelog = generate_changelog_with_llm(therapist_bot, changed_files, project_root, lang)
        
        if should_save_hashes:
            current_hashes = {}
            for rel_path in TRACKED_FILES:
                file_path = project_root / rel_path
                if file_path.exists():
                    current_hashes[rel_path] = calculate_file_hash(file_path)
            save_hashes(data_dir, current_hashes)
            print(f"[CHANGELOG] Hashes saved (should_save_hashes=True)")
        
        return changelog

    except Exception as e:
        print(f"Changelog generation error: {e}")
        raise

def save_current_hashes(project_root: Path) -> None:
    """
    Calculate and save current file hashes.
    This is a convenience function that computes hashes and saves them.
    Should be called after successful changelog notification.
    """
    data_dir = project_root / "data"
    current_hashes = {}
    for rel_path in TRACKED_FILES:
        file_path = project_root / rel_path
        if file_path.exists():
            current_hashes[rel_path] = calculate_file_hash(file_path)
    save_hashes(data_dir, current_hashes)
    print(f"[HASHES] Saved {len(current_hashes)} file hashes to {data_dir / 'code_hashes.json'}")
