"""Compatibility imports and catalogue-check entrypoint for the music feature."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.features.music import Music
from app.features.music.catalogue import CATEGORIES, MAX_BYTES, catalogue
from app.features.music.feature import COLLECTION_INTROS, LABELS, ROOT
from app.features.music.store import Store
from therapist_bot import Message


if __name__ == "__main__":
    from app.features.music.__main__ import main
    main()
