"""
Day 2 integration test - covers the reliability features added on top of
the Day 1 pipeline: real Vani payload shape, webhook signature
verification, idempotency (duplicate webhook delivery), and the failure
paths (empty transcript, missing phone, LLM failure) that must NOT crash
the handler or silently fabricate notes.

Run directly (no live server, no real Vani/Twilio/Gemini needed):
    python test_integration.py
"""

import hashlib
import hmac
import json
import os
import time
from unittest.mock import patch

os.environ.setdefault("DB_PATH", "test_integration_data.json")

import store


def reset_db():
    if os.path.exists(store.DB_PATH):
        os.remove(store.DB_PATH)


def load_mock_payload():
    with open("mock_data/sample_call_analyzed.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_conversation_id_used_as_call_id():
    print("\n--- Test: conversation_id (not envelope id) is used as call_id ---")
    import app
    payload = load_mock_payload()
    result = app.handle_call_analyzed(payload)
    assert result["call_id"] == payload["data"]["conversation_id"], (
        f"Expected call_id to be conversation_id, got {result['call_id']}"
    )
    assert result["call_id"] != payload["id"], "call_id should NOT be the envelope event id"
    print(f"OK - call_id correctly resolved to conversation_id: {result['call_id']}")


def test_webhook_signature_verification():
    print("\n--- Test: webhook signature verification (on) ---")
    os.environ["WEBHOOK_VERIFY_SIGNATURE"] = "true"
    os.environ["VANI_WEBHOOK_SECRET"] = "test_secret_123"
    import importlib
    import app
    importlib.reload(app)

    client = app.app.test_client()
    body = open("mock_data/sample_call_analyzed.json", "rb").read()

    # Bad signature -> rejected
    r = client.post("/webhook/vani", data=body, content_type="application/json",
                     headers={"X-Webhook-Signature": "garbage"})
    assert r.status_code == 401, f"Expected 401 for bad signature, got {r.status_code}"
    print("OK - invalid signature rejected with 401")

    # Correct HMAC-SHA256 hex signature -> accepted
    sig = hmac.new(b"test_secret_123", body, hashlib.sha256).hexdigest()
    r2 = client.post("/webhook/vani", data=body, content_type="application/json",
                      headers={"X-Webhook-Signature": sig})
    assert r2.status_code == 200, f"Expected 200 for valid signature, got {r2.status_code}"
    assert r2.get_json()["status"] == "accepted"
    print("OK - valid HMAC-SHA256 signature accepted")

    os.environ["WEBHOOK_VERIFY_SIGNATURE"] = "false"
    importlib.reload(app)
    time.sleep(0.3)  # let that test's background thread finish before moving on


def test_duplicate_webhook_idempotency():
    print("\n--- Test: duplicate webhook delivery is deduped ---")
    import importlib
    import app
    importlib.reload(app)
    client = app.app.test_client()
    payload = load_mock_payload()

    r1 = client.post("/webhook/vani", json=payload)
    assert r1.get_json()["status"] == "accepted"

    # Give the background thread a moment to process the first delivery
    time.sleep(0.3)

    # Same event id delivered again (simulates Vani's retry-with-backoff)
    r2 = client.post("/webhook/vani", json=payload)
    assert r2.get_json()["status"] == "duplicate", f"Expected duplicate, got {r2.get_json()}"
    print("OK - second delivery of the same event id is recognized as a duplicate")


def test_empty_transcript_does_not_crash():
    print("\n--- Test: empty transcript is handled without crashing ---")
    import app
    payload = load_mock_payload()
    payload["id"] = "evt_empty_test"
    payload["data"] = dict(payload["data"])
    payload["data"]["conversation_id"] = "conv_empty_test"
    payload["data"]["transcript"] = []

    result = app.handle_call_analyzed(payload)
    assert result["status"] == "empty_transcript"
    assert result["notes"] is None
    print("OK - empty transcript returns status=empty_transcript, no crash, no fake notes")


def test_missing_phone_number_skips_sms_not_notes():
    print("\n--- Test: missing phone number skips SMS but still generates notes ---")
    import app
    payload = load_mock_payload()
    payload["id"] = "evt_no_phone_test"
    payload["data"] = dict(payload["data"])
    payload["data"]["conversation_id"] = "conv_no_phone_test"
    payload["data"]["from_number"] = ""
    payload["data"].pop("phone_number", None)
    payload["data"]["qualification_data"] = {}

    result = app.handle_call_analyzed(payload)
    assert result["status"] == "ok", f"Expected ok, got {result}"
    assert result["sms_status"] == "skipped_no_phone"
    assert result["notes"] is not None
    print("OK - notes still generated and retrievable-in-principle even with no phone number")


def test_llm_failure_marks_pending_not_fake_notes():
    print("\n--- Test: LLM failure (with a real key configured) marks notes pending, doesn't fake them ---")
    import llm
    import app
    payload = load_mock_payload()
    payload["id"] = "evt_llm_fail_test"
    payload["data"] = dict(payload["data"])
    payload["data"]["conversation_id"] = "conv_llm_fail_test"

    # Simulate: a real API key IS configured, but every call fails.
    with patch.object(llm, "GEMINI_API_KEY", "fake-but-present-key"), \
         patch.object(llm, "MAX_RETRIES", 2), \
         patch.object(llm, "RETRY_BACKOFF_SECONDS", 0.01), \
         patch("llm._call_gemini", side_effect=llm.LLMError("simulated 500 from Gemini")):
        result = app.handle_call_analyzed(payload)

    assert result["status"] == "notes_pending", f"Expected notes_pending, got {result}"
    assert result["notes"] is None, "Notes must NOT be fabricated when the LLM genuinely fails"
    print("OK - real LLM failure after retries marks notes as pending, does not fabricate content")


def test_notes_page_and_api():
    print("\n--- Test: printable notes page renders and /notes API works end-to-end ---")
    import app
    client = app.app.test_client()

    r = client.get("/")
    assert r.status_code == 200
    assert b"GET NOTES" in r.data
    assert b"PRINT NOTES" in r.data
    print("OK - notes page renders with expected form + print button")

    payload = load_mock_payload()
    payload["id"] = "evt_page_test"
    payload["data"] = dict(payload["data"])
    payload["data"]["conversation_id"] = "conv_page_test"
    result = app.handle_call_analyzed(payload)

    r2 = client.get(f"/notes?phone={result['phone_number']}&code={result['code']}")
    assert r2.status_code == 200
    assert r2.get_json()["notes"] == result["notes"]
    print("OK - /notes API returns the same notes generated by the pipeline")


if __name__ == "__main__":
    reset_db()
    test_conversation_id_used_as_call_id()
    reset_db()
    test_webhook_signature_verification()
    reset_db()
    test_duplicate_webhook_idempotency()
    reset_db()
    test_empty_transcript_does_not_crash()
    reset_db()
    test_missing_phone_number_skips_sms_not_notes()
    reset_db()
    test_llm_failure_marks_pending_not_fake_notes()
    reset_db()
    test_notes_page_and_api()
    reset_db()
    print("\n=== All integration tests passed ===")
