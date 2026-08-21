"""
Person 2's backend: receives Vani's call.analyzed webhook, stores the
transcript, generates revision notes via Gemini, sends the SMS with a
retrieval code, and serves the printable notes webpage.

Verified against Vani's own webhook documentation (Webhooks & Events page):
  - Envelope: {id, type, api_version, created, data}
  - Call identity: data.conversation_id is the STABLE id for a call (not
    the envelope's `id`, which is a per-DELIVERY event id - see comment on
    resolve_call_id below).
  - Signature: X-Webhook-Signature header, HMAC-SHA256 hex digest of the
    RAW request body, keyed by the webhook subscription secret from
    Settings -> Webhooks in the Vani dashboard.
  - Retries: undelivered (non-2xx) webhooks are retried with exponential
    backoff, so the same event id can arrive more than once - handled via
    idempotency below.
  - Best practice: "respond fast, return 200 within 5 seconds, queue work
    asynchronously" - handled by processing in a background thread after
    a fast signature/shape check.
"""

import hashlib
import hmac
import os
import threading

from flask import Flask, request, jsonify, render_template

import store
from notes import generate_notes
from sms import send_sms, build_notes_sms
from llm import LLMError

app = Flask(__name__)

# Webhook signature verification (off by default so local/offline testing
# with curl and test_day1.py needs no secret). Flip WEBHOOK_VERIFY_SIGNATURE
# to "true" and set VANI_WEBHOOK_SECRET (from Vani's Settings -> Webhooks)
# before the real demo.
#
# .strip() on both env vars below: a trailing newline/space from copy-paste
# into Render's dashboard is a common, silent cause of every signature
# check failing - better to normalize it here than debug it blind.
WEBHOOK_VERIFY_SIGNATURE = os.getenv("WEBHOOK_VERIFY_SIGNATURE", "false").strip().lower() == "true"
VANI_WEBHOOK_SECRET = os.getenv("VANI_WEBHOOK_SECRET", "").strip()
# Confirmed from Vani's docs: signed deliveries use this exact header name.
VANI_SIGNATURE_HEADER = os.getenv("VANI_SIGNATURE_HEADER", "X-Webhook-Signature").strip()


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    HMAC-SHA256 hex digest of the raw body, per Vani's docs. Returns True
    if valid (or verification is disabled for local testing).

    Defensive against two common real-world variants beyond Vani's literal
    docs example, since a live integration hit persistent 401s:
      - a scheme prefix on the header value, e.g. "sha256=<hex>" (some
        providers do this even when their docs show bare hex)
      - incidental whitespace around either the header value or the secret
    Logs a safe (non-secret-leaking) diagnostic on every mismatch so a
    failure can be debugged from Render's logs alone.
    """
    if not WEBHOOK_VERIFY_SIGNATURE:
        return True
    if not VANI_WEBHOOK_SECRET or not signature_header:
        print(
            f"[app.py] Signature check failed: "
            f"secret_configured={bool(VANI_WEBHOOK_SECRET)}, "
            f"header_present={bool(signature_header)}"
        )
        return False

    received = signature_header.strip()
    if "=" in received and received.split("=", 1)[0].lower() in ("sha256", "sha1"):
        received = received.split("=", 1)[1].strip()

    expected = hmac.new(
        VANI_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if hmac.compare_digest(expected, received):
        return True

    # Nothing here reveals the secret itself - only lengths and short
    # prefixes, safe to leave in production logs while debugging.
    print(
        "[app.py] Signature mismatch. "
        f"received_len={len(received)} expected_len={len(expected)} "
        f"received_prefix={received[:8]!r} expected_prefix={expected[:8]!r} "
        f"secret_len={len(VANI_WEBHOOK_SECRET)} body_len={len(raw_body)}. "
        "If received_len differs from expected_len, the header format doesn't "
        "match what this code expects. If lengths match but prefixes differ, "
        "the secret itself is likely wrong (re-copy it from Vani's dashboard, "
        "watching for trailing whitespace) - and check whether Vani's webhook "
        "subscription got auto-disabled after repeated failures."
    )
    return False


def resolve_call_id(payload: dict, data: dict) -> str:
    """
    data.conversation_id is "stable across all events for the same call"
    per Vani's docs - the correct identifier to group a call's messages
    and notes under. The envelope's `id` is a per-delivery event id (used
    separately for idempotency, see is_event_processed) - NOT the same
    thing, even though call.analyzed only fires once per call today.
    call_sid (the underlying telephony provider's id) is a last-resort
    fallback if conversation_id is ever missing.
    """
    return (
        data.get("conversation_id")
        or data.get("call_sid")
        or payload.get("id")
        or "unknown_call"
    )


def handle_call_analyzed(payload: dict) -> dict:
    """
    Core pipeline logic, written against Vani's real call.analyzed webhook
    shape. Kept as a plain function (not inline in the route) so it can be
    unit tested directly with a mock payload - see test_day1.py.

    Returns a result dict; never raises for expected failure cases (empty
    transcript, missing phone, LLM failure, SMS failure) - those are
    reflected in the "status" field instead, so a bad call doesn't take
    down the webhook handler.
    """
    data = payload.get("data", {})
    call_id = resolve_call_id(payload, data)
    phone_number = data.get("from_number") or data.get("phone_number") or ""
    messages = data.get("transcript", [])

    store.save_call(call_id, phone_number, messages)

    if not messages:
        store.mark_empty_transcript(call_id)
        return {
            "call_id": call_id,
            "phone_number": phone_number,
            "notes": None,
            "code": None,
            "status": "empty_transcript",
        }

    try:
        notes = generate_notes(messages)
    except LLMError as e:
        # Real LLM failure (key configured, call still failed after
        # retries). Don't fabricate notes - save what we have and flag it
        # so it's visible instead of silently wrong.
        store.mark_notes_pending(call_id, reason=str(e))
        print(f"[app.py] Notes generation pending for {call_id}: {e}")
        return {
            "call_id": call_id,
            "phone_number": phone_number,
            "notes": None,
            "code": None,
            "status": "notes_pending",
        }

    code = store.save_notes_and_code(call_id, notes)

    sms_status = "skipped_no_phone"
    if phone_number:
        sms_body = build_notes_sms(code)
        try:
            send_sms(phone_number, sms_body)
            sms_status = "sent"
        except Exception as e:
            # Notes + code are already saved and retrievable via the
            # webpage even if the SMS itself failed to send.
            sms_status = "failed"
            print(f"[app.py] SMS failed for {call_id}: {e}")

    return {
        "call_id": call_id,
        "phone_number": phone_number,
        "notes": notes,
        "code": code,
        "status": "ok",
        "sms_status": sms_status,
    }


def _process_call_analyzed_async(payload: dict):
    try:
        result = handle_call_analyzed(payload)
        print(f"[app.py] Processed call.analyzed for {result['call_id']}: status={result['status']}")
    except Exception as e:
        # Last-resort catch-all: handle_call_analyzed already handles the
        # expected failure modes above, so anything reaching here is a bug
        # worth logging loudly rather than losing silently in a thread.
        print(f"[app.py] UNEXPECTED error processing call.analyzed: {e}")


@app.route("/webhook/vani", methods=["POST"])
def vani_webhook():
    """
    Real endpoint Person 1 points Vani's webhook config at.

    Only handles call.analyzed - Vani's own docs recommend this as "the
    single event to subscribe to" since it carries the full transcript,
    summary, and extracted fields in one payload (vs. call.started/
    call.ended/etc. which are partial and fire multiple times per call).

    Responds fast (per Vani's "respond within 5 seconds" best practice)
    and does the actual notes/SMS work in a background thread.
    """
    raw_body = request.get_data()
    signature = request.headers.get(VANI_SIGNATURE_HEADER, "")
    if not verify_webhook_signature(raw_body, signature):
        if WEBHOOK_VERIFY_SIGNATURE:
            # Header *names* are never secret - logging them helps catch a
            # wrong VANI_SIGNATURE_HEADER value (e.g. Vani actually sends
            # "X-Vani-Signature" or "X-Signature" instead of the assumed
            # "X-Webhook-Signature") without exposing any values.
            print(f"[app.py] Request headers received: {list(request.headers.keys())}")
        return jsonify({"status": "error", "reason": "invalid webhook signature"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "reason": "malformed JSON payload"}), 400

    event_id = payload.get("id")
    event_type = payload.get("type")

    if event_type != "call.analyzed":
        return jsonify({"status": "ignored", "reason": "not a call.analyzed event"}), 200

    # Idempotency: Vani retries non-2xx deliveries with exponential backoff,
    # so the same event id can legitimately arrive more than once.
    if event_id and store.is_event_processed(event_id):
        return jsonify({"status": "duplicate", "id": event_id}), 200
    if event_id:
        store.mark_event_processed(event_id)

    threading.Thread(target=_process_call_analyzed_async, args=(payload,), daemon=True).start()

    return jsonify({"status": "accepted", "id": event_id}), 200


@app.route("/notes", methods=["GET"])
def get_notes_api():
    """JSON API used by the printable notes page (and any direct caller)."""
    phone_number = request.args.get("phone", "")
    code = request.args.get("code", "")
    if not phone_number or not code:
        return jsonify({"error": "phone and code are required"}), 400
    notes = store.get_notes(phone_number, code)
    if notes is None:
        return jsonify({"error": "No notes found for that phone number and code"}), 404
    return jsonify({"notes": notes}), 200


@app.route("/", methods=["GET"])
def notes_page():
    """The printable "enter phone + code" notes webpage."""
    return render_template("notes.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
