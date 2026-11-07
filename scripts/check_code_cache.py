#!/usr/bin/env python3
"""Read-only inspection of the bot's existing code hash cache."""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))
from code_reviewer import UpdateCache


def check_cache():
    cache = UpdateCache(project_root)
    if not cache.path.exists():
        print(f"Кэш не найден: {cache.path}. При первом запуске будет создана база.")
        return 1
    try:
        data = cache._read()
        current, _ = cache._capture()
        baseline = {key: value for key, value in data.items() if isinstance(value, str)}
        pending = data.get("_update_state", {}).get("pending")
        print(f"Файл кэша: {cache.path}")
        if pending:
            print(f"Черновик: {pending['id'][:8]}, состояние: {pending['status']}")
            print(f"Файлов в черновике: {len(pending['paths'])}")
        elif "_update_state" not in data:
            print("Устаревший формат: новые отслеживаемые файлы получат базу при запуске.")
        changed = [
            path for path in sorted(set(baseline) | set(current))
            if baseline.get(path) != current.get(path)
        ]
        for path in changed:
            print(f"ИЗМЕНЕН: {path}")
        print(f"Изменений относительно базы: {len(changed)}")
        return 1 if changed or pending else 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Не удалось прочитать кэш: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(check_cache())
