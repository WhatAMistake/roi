"""
Точка входа для запуска бота.
"""

import sys
from pathlib import Path

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / "src"))

from therapist_bot import main

if __name__ == "__main__":
    main()