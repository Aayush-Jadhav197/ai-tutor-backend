"""
Storage layer for the Vani -> Gemini revision-notes backend.

Stores:
- call transcript
- generated revision notes
- processing status
- webhook event ids for idempotency

The website now retrieves the latest notes using only the phone number.
No SMS code is required.
"""

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone


# On Render, use /tmp unless DATABASE_PATH is explicitly configured.
# For local development this creates notes.db in the project directory.
DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(__file__), "notes.db"),
)

_db_lock = threading.Lock()


def _get_connection():
    conn = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    """Create all required tables if they do not already exist."""

    directory = os.path.dirname(DATABASE_PATH)

    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass

    with _db_lock:
        conn = _get_connection()

        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY,
                    phone_number TEXT NOT NULL DEFAULT '',
                    transcript TEXT NOT NULL DEFAULT '[]',
                    notes TEXT,
                    code TEXT,
                    status TEXT NOT NULL DEFAULT 'received',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_calls_phone
                ON calls(phone_number)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_calls_phone_updated
                ON calls(phone_number, updated_at DESC)
                """
            )

            conn.commit()

        finally:
            conn.close()


def _normalize_phone(phone_number):
    """Normalize phone input enough for reliable lookup."""

    if phone_number is None:
        return ""

    return str(phone_number).strip()


def save_call(call_id, phone_number, messages):
    """
    Save the transcript received from Vani.

    This is called before notes generation, so even if Gemini fails,
    the original call/transcript is still stored.
    """

    call_id = str(call_id)
    phone_number = _normalize_phone(phone_number)
    messages = messages or []

    now = _now()
    transcript_json = json.dumps(
        messages,
        ensure_ascii=False,
    )

    with _db_lock:
        conn = _get_connection()

        try:
            existing = conn.execute(
                """
                SELECT call_id
                FROM calls
                WHERE call_id = ?
                """,
                (call_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE calls
                    SET phone_number = ?,
                        transcript = ?,
                        status = ?,
                        updated_at = ?
                    WHERE call_id = ?
                    """,
                    (
                        phone_number,
                        transcript_json,
                        "received",
                        now,
                        call_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO calls (
                        call_id,
                        phone_number,
                        transcript,
                        notes,
                        code,
                        status,
                        error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, NULL, NULL, ?, NULL, ?, ?)
                    """,
                    (
                        call_id,
                        phone_number,
                        transcript_json,
                        "received",
                        now,
                        now,
                    ),
                )

            conn.commit()

        finally:
            conn.close()


def mark_empty_transcript(call_id):
    """Mark a call where Vani sent no transcript."""

    with _db_lock:
        conn = _get_connection()

        try:
            conn.execute(
                """
                UPDATE calls
                SET status = ?,
                    updated_at = ?
                WHERE call_id = ?
                """,
                (
                    "empty_transcript",
                    _now(),
                    str(call_id),
                ),
            )

            conn.commit()

        finally:
            conn.close()


def mark_notes_pending(call_id, reason=""):
    """
    Mark notes generation as pending/failed.

    The transcript remains stored so the call is not lost.
    """

    with _db_lock:
        conn = _get_connection()

        try:
            conn.execute(
                """
                UPDATE calls
                SET status = ?,
                    error = ?,
                    updated_at = ?
                WHERE call_id = ?
                """,
                (
                    "notes_pending",
                    str(reason)[:2000],
                    _now(),
                    str(call_id),
                ),
            )

            conn.commit()

        finally:
            conn.close()


def save_notes_and_code(call_id, notes):
    """
    Save generated notes.

    `code` is retained for backwards compatibility with the existing
    backend, but the website no longer requires it.
    """

    # Keep a code in the database so older tests/functions do not break.
    code = secrets.token_hex(4).upper()

    with _db_lock:
        conn = _get_connection()

        try:
            conn.execute(
                """
                UPDATE calls
                SET notes = ?,
                    code = ?,
                    status = ?,
                    error = NULL,
                    updated_at = ?
                WHERE call_id = ?
                """,
                (
                    notes,
                    code,
                    "notes_ready",
                    _now(),
                    str(call_id),
                ),
            )

            conn.commit()

        finally:
            conn.close()

    return code


def get_latest_notes(phone_number):
    """
    Return the latest generated notes for this phone number.

    This is the main function used by the website.

    No retrieval code is required.
    """

    phone_number = _normalize_phone(phone_number)

    if not phone_number:
        return None

    with _db_lock:
        conn = _get_connection()

        try:
            row = conn.execute(
                """
                SELECT notes
                FROM calls
                WHERE phone_number = ?
                  AND notes IS NOT NULL
                  AND status = 'notes_ready'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (phone_number,),
            ).fetchone()

            if row is None:
                return None

            return row["notes"]

        finally:
            conn.close()


def get_notes(phone_number, code=None):
    """
    Backwards-compatible notes lookup.

    Old code may still call get_notes(phone, code).
    The code is now optional/ignored because the new website
    uses phone number only.
    """

    return get_latest_notes(phone_number)


def is_event_processed(event_id):
    """Return True if this Vani webhook event was already processed."""

    if not event_id:
        return False

    with _db_lock:
        conn = _get_connection()

        try:
            row = conn.execute(
                """
                SELECT event_id
                FROM processed_events
                WHERE event_id = ?
                """,
                (str(event_id),),
            ).fetchone()

            return row is not None

        finally:
            conn.close()


def mark_event_processed(event_id):
    """Record a Vani webhook event for idempotency."""

    if not event_id:
        return

    with _db_lock:
        conn = _get_connection()

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_events (
                    event_id,
                    processed_at
                )
                VALUES (?, ?)
                """,
                (
                    str(event_id),
                    _now(),
                ),
            )

            conn.commit()

        finally:
            conn.close()


def get_call(call_id):
    """Optional helper for debugging/admin use."""

    with _db_lock:
        conn = _get_connection()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM calls
                WHERE call_id = ?
                """,
                (str(call_id),),
            ).fetchone()

            if row is None:
                return None

            result = dict(row)

            try:
                result["transcript"] = json.loads(
                    result.get("transcript") or "[]"
                )
            except (TypeError, json.JSONDecodeError):
                result["transcript"] = []

            return result

        finally:
            conn.close()


# Initialize database when this module is imported.
init_db()
