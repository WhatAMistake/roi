"""Feature gate and rate limiting for film-frame."""

import time
from datetime import datetime, timedelta
from typing import Optional
from .config import (
    FILM_FRAME_ENABLED,
    FILM_FRAME_ALLOWED_USER_IDS,
    FILM_FRAME_PER_USER_DAILY_LIMIT,
    FILM_FRAME_GLOBAL_DAILY_LIMIT,
)

# In-memory counters (reset on restart — acceptable for experimental feature)
_user_daily_counts: dict[int, int] = {}
_user_daily_reset: dict[int, float] = {}
_global_daily_count: int = 0
_global_daily_reset: float = 0.0


def _day_key() -> str:
    """Return a key that changes at midnight (UTC+3 Moscow)."""
    now = datetime.now()
    return now.strftime("%Y-%m-%d")


def _is_admin(user_id: int, admin_id: int = 0) -> bool:
    """True for the admin, who bypasses everything as before."""
    return bool(admin_id) and user_id == admin_id


def _ignores_limits(user_id: int, admin_id: int = 0) -> bool:
    """True if the user bypasses daily limits (admin or explicitly allowed)."""
    return _is_admin(user_id, admin_id) or user_id in FILM_FRAME_ALLOWED_USER_IDS


def is_feature_available(user_id: int, admin_id: int = 0) -> tuple[bool, Optional[str]]:
    """Check if film-frame is available for this user.

    Returns (allowed, error_message).
    Admin bypasses everything; explicitly allowed IDs bypass daily limits only.
    """
    if _is_admin(user_id, admin_id):
        return (True, None)

    if not FILM_FRAME_ENABLED:
        return (False, "feature_disabled")

    if _ignores_limits(user_id, admin_id):
        return (True, None)

    # Check per-user daily limit
    today = _day_key()
    if _user_daily_reset.get(user_id, "") != today:
        _user_daily_counts[user_id] = 0
        _user_daily_reset[user_id] = today

    if _user_daily_counts.get(user_id, 0) >= FILM_FRAME_PER_USER_DAILY_LIMIT:
        return (False, "user_limit_reached")

    # Check global daily limit
    global _global_daily_count, _global_daily_reset
    if _global_daily_reset != today:
        _global_daily_count = 0
        _global_daily_reset = today

    if _global_daily_count >= FILM_FRAME_GLOBAL_DAILY_LIMIT:
        return (False, "global_limit_reached")

    return (True, None)


def record_usage(user_id: int, admin_id: int = 0) -> None:
    """Increment per-user and global daily counters.

    Unbounded users (admin / allowed IDs) are not counted against limits.
    """
    if _ignores_limits(user_id, admin_id):
        return

    global _global_daily_count, _global_daily_reset
    today = _day_key()
    if _user_daily_reset.get(user_id, "") != today:
        _user_daily_counts[user_id] = 0
        _user_daily_reset[user_id] = today
    if _global_daily_reset != today:
        _global_daily_count = 0
        _global_daily_reset = today
    _user_daily_counts[user_id] = _user_daily_counts.get(user_id, 0) + 1
    _global_daily_count += 1