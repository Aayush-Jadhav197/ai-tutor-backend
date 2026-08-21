"""
Person 2's backend:
receives Vani's call.analyzed webhook, stores the transcript,
generates revision notes via Gemini, and serves the notes webpage.

Current flow:
    Vani call
        -> webhook
        -> transcript
        -> Gemini revision notes
        -> save notes
        -> website
        -> user enters phone number
        -> notes displayed

SMS/Twilio has intentionally been removed.
"""

import hashlib
import hmac
import os
import threading

from flask import Flask, request, jsonify, render_template

import store
from notes import generate_notes
from llm import LLMError


app = Flask(__name__)


# ============================================================
# WEBHOOK CONFIG
# ============================================================

WEBHOOK_VERIFY_SIGNATURE = (
    os.getenv("WEBHOOK_VERIFY_SIGNATURE", "false")
    .strip()
    .lower()
    == "true"
)

VANI_WEBHOOK_SECRET = os.getenv(
    "VANI_WEBHOOK_SECRET",
    ""
).strip()

VANI_SIGNATURE_HEADER = os.getenv(
    "VANI_SIGNATURE_HEADER",
    "X-Webhook-Signature"
).strip()


# ============================================================
# TEMPORARY LATEST NOTES CACHE
# ============================================================
#
# SMS hata diya hai.
# Isliye demo ke liye latest generated notes ko phone number ke
# against memory me rakh rahe hain.
#
# Example:
# {
#     "+916265763663": "Revision notes..."
# }
#
# New Render deployment ke baad purane in-memory notes clear ho
# jayenge, lekin naye calls ke notes normally kaam karenge.
#
# Deadline ke baad isko proper DB lookup me shift kar sakte hain.
#

LATEST_NOTES = {}


# ============================================================
# HELPERS
# ============================================================

def normalize_phone(phone_number: str) -> str:
    """
    Normalize phone number just enough for consistent lookup.

    We keep the +country-code format when provided.
    """
    if not phone_number:
        return ""

    phone_number = str(phone_number).strip()

    # Remove common formatting characters.
    phone_number = (
        phone_number
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    return phone_number


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str
) -> bool:
    """
    Verify Vani webhook HMAC-SHA256 signature.

    Verification can be disabled for local testing by leaving:
        WEBHOOK_VERIFY_SIGNATURE=false
    """

    if not WEBHOOK_VERIFY_SIGNATURE:
        return True

    if not VANI_WEBHOOK_SECRET or not signature_header:
        print(
            "[app.py] Signature check failed: "
            f"secret_configured={bool(VANI_WEBHOOK_SECRET)}, "
            f"header_present={bool(signature_header)}"
        )
        return False

    received = signature_header.strip()

    # Support:
    #   abc123...
    # and:
    #   sha256=abc123...
    if "=" in received:
        prefix, value = received.split("=", 1)

        if prefix.lower() in ("sha256", "sha1"):
            received = value.strip()

    expected = hmac.new(
        VANI_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if hmac.compare_digest(expected, received):
        return True

    print(
        "[app.py] Signature mismatch. "
        f"received_len={len(received)} "
        f"expected_len={len(expected)} "
        f"received_prefix={received[:8]!r} "
        f"expected_prefix={expected[:8]!r} "
        f"secret_len={len(VANI_WEBHOOK_SECRET)} "
        f"body_len={len(raw_body)}"
    )

    return False


def resolve_call_id(payload: dict, data: dict) -> str:
    """
    conversation_id is the preferred stable call identifier.
    """

    return (
        data.get("conversation_id")
        or data.get("call_sid")
        or payload.get("id")
        or "unknown_call"
    )


def extract_phone_number(data: dict) -> str:
    """
    Extract the phone number from Vani's actual webhook payload.

    In the real webhook we observed:

        recipient_phone = +916265763663
        to_number = +916265763663
        variables.phone_number = +916265763663

    while from_number was empty.

    Therefore recipient_phone / to_number are checked first.
    """

    variables = data.get("variables") or {}

    phone_number = (
        data.get("recipient_phone")
        or data.get("to_number")
        or data.get("phone_number")
        or variables.get("phone_number")
        or variables.get("recipient_phone_number")
        or data.get("from_number")
        or data.get("caller_number")
        or data.get("from")
        or ""
    )

    return normalize_phone(phone_number)


# ============================================================
# CALL PROCESSING
# ============================================================

def handle_call_analyzed(payload: dict) -> dict:
    """
    Process one call.analyzed event.

    Pipeline:

        webhook
        -> phone extraction
        -> transcript save
        -> Gemini notes
        -> notes save
        -> latest notes cache
    """

    data = payload.get("data", {})

    print(f"[DEBUG] Event type: {payload.get('type')}")
    print(f"[DEBUG] Data keys: {list(data.keys())}")
    print(f"[DEBUG] Full data: {data}")

    call_id = resolve_call_id(payload, data)

    phone_number = extract_phone_number(data)

    print(f"[DEBUG] Extracted phone number: {phone_number}")

    messages = data.get("transcript", [])

    print(f"[DEBUG] Transcript type: {type(messages)}")
    print(
        f"[DEBUG] Transcript length: "
        f"{len(messages) if messages else 0}"
    )

    # Save transcript.
    store.save_call(
        call_id,
        phone_number,
        messages
    )

    # --------------------------------------------------------
    # Empty transcript
    # --------------------------------------------------------

    if not messages:
        store.mark_empty_transcript(call_id)

        return {
            "call_id": call_id,
            "phone_number": phone_number,
            "notes": None,
            "code": None,
            "status": "empty_transcript",
        }

    # --------------------------------------------------------
    # Generate notes
    # --------------------------------------------------------

    try:
        print(
            f"[DEBUG] Starting notes generation for {call_id}"
        )

        notes = generate_notes(messages)

        print(
            f"[DEBUG] Notes generated successfully for {call_id}"
        )

    except LLMError as e:

        store.mark_notes_pending(
            call_id,
            reason=str(e)
        )

        print(
            f"[app.py] Notes generation pending "
            f"for {call_id}: {e}"
        )

        return {
            "call_id": call_id,
            "phone_number": phone_number,
            "notes": None,
            "code": None,
            "status": "notes_pending",
        }

    # --------------------------------------------------------
    # Save notes
    # --------------------------------------------------------

    # Existing DB function can remain.
    # We don't use the generated code for the website anymore,
    # but keeping this call preserves the existing DB behaviour.
    code = store.save_notes_and_code(
        call_id,
        notes
    )

    # --------------------------------------------------------
    # Save latest notes for phone-number-only website lookup
    # --------------------------------------------------------

    if phone_number:
        LATEST_NOTES[phone_number] = notes

        print(
            f"[DEBUG] Latest notes stored for phone: "
            f"{phone_number}"
        )
    else:
        print(
            "[WARNING] No phone number found; "
            "notes cannot be accessed through phone lookup."
        )

    return {
        "call_id": call_id,
        "phone_number": phone_number,
        "notes": notes,
        "code": code,
        "status": "ok",
    }


# ============================================================
# ASYNC PROCESSING
# ============================================================

def _process_call_analyzed_async(payload: dict):
    try:

        result = handle_call_analyzed(payload)

        print(
            f"[app.py] Processed call.analyzed "
            f"for {result['call_id']}: "
            f"status={result['status']}"
        )

    except Exception as e:

        print(
            f"[app.py] UNEXPECTED error processing "
            f"call.analyzed: {e}"
        )


# ============================================================
# VANI WEBHOOK
# ============================================================

@app.route(
    "/webhook/vani",
    methods=["POST"]
)
def vani_webhook():
    """
    Vani webhook endpoint.

    Only call.analyzed events are processed.
    """

    raw_body = request.get_data()

    signature = request.headers.get(
        VANI_SIGNATURE_HEADER,
        ""
    )

    # --------------------------------------------------------
    # Signature verification
    # --------------------------------------------------------

    if not verify_webhook_signature(
        raw_body,
        signature
    ):

        if WEBHOOK_VERIFY_SIGNATURE:
            print(
                "[app.py] Request headers received: "
                f"{list(request.headers.keys())}"
            )

        return jsonify({
            "status": "error",
            "reason": "invalid webhook signature"
        }), 401

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    payload = request.get_json(
        silent=True
    )

    if not isinstance(payload, dict):

        return jsonify({
            "status": "error",
            "reason": "malformed JSON payload"
        }), 400

    event_id = payload.get("id")
    event_type = payload.get("type")

    # --------------------------------------------------------
    # Only call.analyzed
    # --------------------------------------------------------

    if event_type != "call.analyzed":

        return jsonify({
            "status": "ignored",
            "reason": "not a call.analyzed event"
        }), 200

    # --------------------------------------------------------
    # Idempotency
    # --------------------------------------------------------

    if event_id and store.is_event_processed(event_id):

        return jsonify({
            "status": "duplicate",
            "id": event_id
        }), 200

    if event_id:
        store.mark_event_processed(event_id)

    # --------------------------------------------------------
    # Background processing
    # --------------------------------------------------------

    threading.Thread(
        target=_process_call_analyzed_async,
        args=(payload,),
        daemon=True
    ).start()

    # Respond immediately to Vani.
    return jsonify({
        "status": "accepted",
        "id": event_id
    }), 200


# ============================================================
# NOTES API
# ============================================================

@app.route(
    "/notes",
    methods=["GET"]
)
def get_notes_api():
    """
    Get latest notes using ONLY phone number.

    Example:
        /notes?phone=+916265763663
    """

    phone_number = request.args.get(
        "phone",
        ""
    ).strip()

    phone_number = normalize_phone(
        phone_number
    )

    if not phone_number:

        return jsonify({
            "error": "phone is required"
        }), 400

    notes = LATEST_NOTES.get(
        phone_number
    )

    if notes is None:

        return jsonify({
            "error": (
                "No notes found for this phone number. "
                "Please make a call first and try again."
            )
        }), 404

    return jsonify({
        "notes": notes
    }), 200


# ============================================================
# WEBSITE
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def notes_page():
    """
    Printable notes webpage.
    """

    return render_template(
        "notes.html"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "status": "ok"
    }), 200


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),
        debug=True
    )
