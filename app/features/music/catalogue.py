"""Manifest validation and private audio catalogue."""

import json
import logging
from pathlib import Path, PurePosixPath

LOG = logging.getLogger(__name__)
CATEGORIES = ("death", "freedom", "isolation", "meaninglessness")
MAX_BYTES = 50 * 1024 * 1024


def catalogue(manifest, directory):
    """Return configured entries and playable entries. Invalid entries are skipped."""
    configured, playable, ids = [], [], set()
    root = Path(directory).resolve()
    try:
        entries = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("manifest is not a list")
    except (OSError, ValueError, TypeError):
        LOG.warning("Music manifest unavailable or invalid")
        return configured, playable
    for item in entries:
        if not isinstance(item, dict):
            continue
        ident, category = item.get("id"), item.get("category")
        if not isinstance(ident, str) or not ident or ident in ids or category not in CATEGORIES:
            continue
        ids.add(ident)
        configured.append(item)
        filename, title, artist = item.get("file"), item.get("title"), item.get("artist")
        if not all(isinstance(x, str) and x.strip() for x in (filename, title, artist)):
            continue
        # Preserve filenames; reject traversal, absolute paths and escaping symlinks.
        rel = PurePosixPath(filename)
        if (rel.is_absolute() or "\\" in filename or "\x00" in filename or
                any(part in (".", "..") for part in rel.parts) or rel.suffix.lower() != ".mp3"):
            continue
        try:
            path = (root / Path(*rel.parts)).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
                continue
            with path.open("rb") as source:
                header = source.read(3)
            if not (header == b"ID3" or (len(header) >= 2 and header[0] == 0xff and header[1] & 0xe0 == 0xe0)):
                continue
        except OSError:
            continue
        playable.append({**item, "path": path})
    return configured, playable
