"""Durable music history and atomic selection reservations."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .catalogue import CATEGORIES


class Store:
    """Each transaction is short; a reserved row survives process crashes."""

    def __init__(self, path, key):
        if not key:
            raise ValueError("music requires USER_PREFS_ENCRYPTION_KEY")
        self.key = key.encode() if isinstance(key, str) else key
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS music_delivery (
                    user_key TEXT NOT NULL, track_id TEXT NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY(user_key, track_id));
                CREATE TABLE IF NOT EXISTS music_user (
                    user_key TEXT PRIMARY KEY, session INTEGER NOT NULL DEFAULT 0,
                    offered_session INTEGER, last_offer REAL,
                    initiative_enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS music_button (
                    token TEXT PRIMARY KEY, user_key TEXT NOT NULL, session INTEGER NOT NULL,
                    kind TEXT NOT NULL, category TEXT, created REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS music_file (
                    track_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, file_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS music_meta (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            # Existing installations may have a music_user table without this preference.
            columns = {row[1] for row in db.execute("PRAGMA table_info(music_user)")}
            if "initiative_enabled" not in columns:
                db.execute("ALTER TABLE music_user ADD COLUMN initiative_enabled INTEGER NOT NULL DEFAULT 1")
            verifier = hmac.new(self.key, b"roi-music-key-check-v1", hashlib.sha256).hexdigest()
            db.execute("INSERT OR IGNORE INTO music_meta VALUES ('key_check', ?)", (verifier,))
            saved = db.execute("SELECT value FROM music_meta WHERE name='key_check'").fetchone()[0]
            if not hmac.compare_digest(saved, verifier):
                raise ValueError("music database key does not match")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def user(self, uid):
        return hmac.new(self.key, ("roi-music-v1:" + str(uid)).encode(), hashlib.sha256).hexdigest()

    def delete_user(self, uid):
        """Delete only this user's HMAC-linked music records in one transaction."""
        key = self.user(uid)
        with self.connect() as db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            for table in ("music_delivery", "music_button", "music_user"):
                db.execute(f"DELETE FROM {table} WHERE user_key=?", (key,))
        # In WAL mode old frames can contain earlier versions of deleted rows.
        with self.connect() as db:
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy:
                    raise RuntimeError("Music checkpoint was busy")

    def unavailable(self, uid):
        with self.connect() as db:
            return {r[0] for r in db.execute("SELECT track_id FROM music_delivery WHERE user_key=?", (self.user(uid),))}

    def sent(self, uid):
        with self.connect() as db:
            return {r[0] for r in db.execute(
                "SELECT track_id FROM music_delivery WHERE user_key=? AND status='sent'", (self.user(uid),))}

    def may_offer(self, uid, cooldown):
        """Fast preflight; claim_initiative() is the authoritative atomic gate."""
        with self.connect() as db:
            row = db.execute(
                "SELECT session, offered_session, last_offer, initiative_enabled "
                "FROM music_user WHERE user_key=?", (self.user(uid),)).fetchone()
        return not row or (bool(row[3]) and row[0] != row[1] and
                           (row[2] is None or time.time() - row[2] >= cooldown))

    def claim_initiative(self, uid, cooldown):
        """Allow one automatic music delivery per session and cooldown, atomically."""
        key, now = self.user(uid), time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO music_user(user_key) VALUES (?)", (key,))
            session, sent_session, last, enabled = db.execute(
                "SELECT session, offered_session, last_offer, initiative_enabled "
                "FROM music_user WHERE user_key=?", (key,)).fetchone()
            if not enabled or sent_session == session or (last is not None and now - last < cooldown):
                return False
            db.execute("UPDATE music_user SET offered_session=?, last_offer=? WHERE user_key=?",
                       (session, now, key))
            return now

    def release_initiative(self, uid, claimed_at):
        """Undo a failed automatic attempt only if it is still the active claim."""
        with self.connect() as db:
            db.execute("""UPDATE music_user SET offered_session=NULL, last_offer=NULL
                WHERE user_key=? AND last_offer=? AND offered_session=session""",
                (self.user(uid), claimed_at))

    def set_initiative(self, uid, enabled):
        """User preference affects spontaneous sends only, never the /music command."""
        key = self.user(uid)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO music_user(user_key) VALUES (?)", (key,))
            db.execute("UPDATE music_user SET initiative_enabled=? WHERE user_key=?",
                       (int(enabled), key))

    def reserve(self, uid, tracks):
        key = self.user(uid)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            unavailable = {r[0] for r in db.execute("SELECT track_id FROM music_delivery WHERE user_key=?", (key,))}
            candidates = [track for track in tracks if track["id"] not in unavailable]
            if not candidates:
                return None
            track = secrets.choice(candidates)
            db.execute("INSERT INTO music_delivery VALUES (?, ?, 'reserved')", (key, track["id"]))
            return track

    def finish(self, uid, track_id, success):
        with self.connect() as db:
            if success:
                db.execute("UPDATE music_delivery SET status='sent' WHERE user_key=? AND track_id=? AND status='reserved'",
                           (self.user(uid), track_id))
            else:
                db.execute("DELETE FROM music_delivery WHERE user_key=? AND track_id=? AND status='reserved'",
                           (self.user(uid), track_id))

    def reset_session(self, uid):
        key = self.user(uid)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO music_user(user_key) VALUES (?)", (key,))
            db.execute("UPDATE music_user SET session=session+1 WHERE user_key=?", (key,))
            db.execute("UPDATE music_button SET used=1 WHERE user_key=?", (key,))

    def invalidate_buttons(self, uid):
        with self.connect() as db:
            db.execute("UPDATE music_button SET used=1 WHERE user_key=?", (self.user(uid),))

    def button(self, uid):
        """One-time buttons for explicit category selection (/music), not for consent."""
        key, now, token = self.user(uid), time.time(), secrets.token_urlsafe(12)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO music_user(user_key) VALUES (?)", (key,))
            session = db.execute(
                "SELECT session FROM music_user WHERE user_key=?", (key,)).fetchone()[0]
            db.execute("UPDATE music_button SET used=1 WHERE user_key=?", (key,))
            db.execute("INSERT INTO music_button VALUES (?, ?, ?, ?, ?, ?, 0)",
                       (token, key, session, "choose", None, now))
            return token

    def consume(self, uid, token, action):
        key = self.user(uid)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT b.kind, b.category FROM music_button b JOIN music_user u
                ON b.user_key=u.user_key AND b.session=u.session
                WHERE b.token=? AND b.user_key=? AND b.used=0 AND b.created>?""",
                (token, key, time.time() - 86400)).fetchone()
            if not row or row[0] != "choose" or action not in CATEGORIES:
                return None
            db.execute("UPDATE music_button SET used=1 WHERE token=?", (token,))
            return row

    def file_id(self, track):
        stat = track["path"].stat()
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}:{track['file']}"
        with self.connect() as db:
            row = db.execute("SELECT file_id FROM music_file WHERE track_id=? AND fingerprint=?",
                             (track["id"], fingerprint)).fetchone()
        return fingerprint, row[0] if row else None

    def save_file_id(self, track_id, fingerprint, file_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO music_file VALUES (?, ?, ?)", (track_id, fingerprint, file_id))

    def forget_file_id(self, track_id):
        with self.connect() as db:
            db.execute("DELETE FROM music_file WHERE track_id=?", (track_id,))
