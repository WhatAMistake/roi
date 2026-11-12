"""Experimental logging for film-frame runs."""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("filmframe.logger")

# Log file path
LOG_DIR = Path(__file__).parent.parent.parent.parent / "data"
FILM_FRAME_LOG_FILE = LOG_DIR / "film_frame_runs.jsonl"
_log_lock = threading.RLock()


def log_run(
    user_id: int,
    description: str,
    preview: str,
    image_prompt: str,
    model: str,
    seed: Optional[int],
    latency_ms: int,
    status: str,
    error_code: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> None:
    """Append a single run record to film_frame_runs.jsonl.

    Never logs API keys or temporary provider URLs.
    """
    if timestamp is None:
        timestamp = datetime.now()

    record = {
        "user_id": user_id,
        "description": description[:500],
        "preview": preview[:500],
        "image_prompt": image_prompt[:1000],
        "model": model,
        "seed": seed,
        "latency_ms": latency_ms,
        "status": status,
        "error_code": error_code,
        "timestamp": timestamp.isoformat(),
    }

    try:
        with _log_lock:
            FILM_FRAME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FILM_FRAME_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed to write film_frame_runs log: {e}")


def trim_old_logs(retention_days: int = 90) -> None:
    """Remove log entries older than retention_days.

    This mirrors the existing user data retention lifecycle.
    """
    with _log_lock:
        if not FILM_FRAME_LOG_FILE.exists():
            return
        cutoff = datetime.now().timestamp() - (retention_days * 86400)
        try:
            lines = FILM_FRAME_LOG_FILE.read_text(encoding="utf-8").strip().split("\n")
            kept = []
            for line in lines:
                try:
                    record = json.loads(line)
                    ts = datetime.fromisoformat(record.get("timestamp", ""))
                    if ts.timestamp() >= cutoff:
                        kept.append(line)
                except Exception:
                    kept.append(line)
            FILM_FRAME_LOG_FILE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to trim film_frame_runs logs: {e}")


def delete_user(user_id: int) -> None:
    """Atomically filter a user's records; fail closed on unrecognizable lines."""
    with _log_lock:
        if not FILM_FRAME_LOG_FILE.exists():
            return
        lines = FILM_FRAME_LOG_FILE.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or "user_id" not in record:
                raise ValueError("Unrecognizable film-frame record")
            if str(record["user_id"]) != str(user_id):
                kept.append(line)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=FILM_FRAME_LOG_FILE.parent,
                prefix=".film-frame-", suffix=".tmp", delete=False,
            ) as handle:
                temp = handle.name
                handle.write("\n".join(kept) + ("\n" if kept else ""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, FILM_FRAME_LOG_FILE)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
