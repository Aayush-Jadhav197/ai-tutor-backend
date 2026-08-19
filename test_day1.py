"""
Day 1 goal, per the plan:
  mock transcript -> notes generated -> conversation stored -> SMS/code works

Run this directly (no server, no Vani, no Twilio needed):
    python test_day1.py
"""

import json

from app import handle_call_analyzed
import store


def main():
    with open("mock_data/sample_call_analyzed.json", "r", encoding="utf-8") as f:
        payload = json.load(f)

    result = handle_call_analyzed(payload)

    print("=== Pipeline result ===")
    print(f"call_id:      {result['call_id']}")
    print(f"phone_number: {result['phone_number']}")
    print(f"code:         {result['code']}")
    print("\n=== Generated notes ===")
    print(result["notes"])

    print("\n=== Retrieval check ===")
    fetched = store.get_notes(result["phone_number"], result["code"])
    assert fetched == result["notes"], "Retrieval mismatch!"
    print("OK - notes retrievable by phone_number + code.")


if __name__ == "__main__":
    main()
