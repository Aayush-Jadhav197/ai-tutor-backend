"""
Deliberately simple storage for a 4-day prototype: one JSON file on disk,
loaded into memory and rewritten on every change. No database needed.

Shape:
{
  "calls": {
     "<call_id>": {
        "phone_number": "...",
        "messages": [...],
        "notes": "...",
        "code": "482731",
        "notes_status": "ok" | "pending" | "empty_transcript",
        "notes_error": "..."          # only set when notes_status == "pending"
     }
  },
  "codes": {
     "9876543210:482731": "<call_id>"   # for fast retrieval lookup
  },
  "processed_event_ids": {
     "evt_...": true    # webhook delivery IDs already handled - dedup guard
                          # against Vani's retry-with-backoff redelivering
                          # the same event (see Vani's "Retry behavior" docs)
  }
}
"""

import json
import os
import random
import threading

DB_PATH = os.getenv("DB_PATH", "data.json")
_lock = threading.Lock()


def _load():
    if not os.path.exists(DB_PATH):
        return {"calls": {}, "codes": {}, "processed_event_ids": {}}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        db = json.load(f)
        db.setdefault("processed_event_ids", {})
        return db


def _save(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


# ---------- Idempotency (webhook delivery dedup) ----------

def is_event_processed(event_id: str) -> bool:
    if not event_id:
        return False
    db = _load()
    return event_id in db["processed_event_ids"]


def mark_event_processed(event_id: str):
    if not event_id:
        return
    with _lock:
        db = _load()
        db["processed_event_ids"][event_id] = True
        _save(db)


# ---------- Calls / conversations ----------

def save_call(call_id: str, phone_number: str, messages: list):
    with _lock:
        db = _load()
        call = db["calls"].setdefault(
            call_id,
            {
                "phone_number": phone_number,
                "messages": [],
                "notes": None,
                "code": None,
                "notes_status": None,
                "notes_error": None,
            },
        )
        call["phone_number"] = phone_number
        call["messages"] = messages
        _save(db)


def save_notes_and_code(call_id: str, notes: str) -> str:
    with _lock:
        db = _load()
        call = db["calls"][call_id]
        code = f"{random.randint(0, 999999):06d}"
        call["notes"] = notes
        call["code"] = code
        call["notes_status"] = "ok"
        call["notes_error"] = None
        db["codes"][f"{call['phone_number']}:{code}"] = call_id
        _save(db)
        return code


def mark_notes_pending(call_id: str, reason: str = ""):
    """
    Used when the LLM call fails after retries (see llm.py). The transcript
    is already saved via save_call() - this just flags that notes generation
    didn't complete, instead of silently producing fake ones. No code is
    generated yet since there's nothing to retrieve.
    """
    with _lock:
        db = _load()
        call = db["calls"].setdefault(
            call_id,
            {
                "phone_number": None,
                "messages": [],
                "notes": None,
                "code": None,
                "notes_status": None,
                "notes_error": None,
            },
        )
        call["notes_status"] = "pending"
        call["notes_error"] = reason
        _save(db)


def mark_empty_transcript(call_id: str):
    with _lock:
        db = _load()
        call = db["calls"].setdefault(
            call_id,
            {
                "phone_number": None,
                "messages": [],
                "notes": None,
                "code": None,
                "notes_status": None,
                "notes_error": None,
            },
        )
        call["notes_status"] = "empty_transcript"
        _save(db)


def get_notes(phone_number: str, code: str):
    db = _load()
    call_id = db["codes"].get(f"{phone_number}:{code}")
    if not call_id:
        return None
    return db["calls"][call_id]["notes"]
