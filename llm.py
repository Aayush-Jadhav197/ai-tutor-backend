"""
Notes-generation LLM client.

Vani's own dashboard doesn't list Featherless as an LLM provider, so the LIVE
tutor conversation runs inside Vani on its built-in model (Gemini 2.5
Flash-Lite, per your Vani agent config). This module is NOT for the live
conversation - that prompt lives in vani_system_prompt.txt and gets pasted
into the Vani agent config, untouched by anything here.

This module is for the ONE THING that still needs our own LLM call:
turning a finished call transcript into short structured revision notes,
after Vani sends us the call.analyzed webhook.

Default provider: Featherless. Switched from Gemini after repeated
real-world friction that had nothing to do with our code - Google API key
format changes (new "AQ."-prefixed keys not working with the standard REST
endpoint) and a model retirement (gemini-2.0-flash returning 404) both hit
mid-build. Featherless doesn't have either failure mode: a plain bearer
token, no Google-side model churn. Gemini support is left in place and
still works if NOTES_LLM_PROVIDER is switched back - just not the default
anymore.

Error handling policy (important - read before changing):
  - No API key configured at all -> this is expected in local/offline dev
    (test_day1.py, no .env yet). Return the canned mock immediately so the
    rest of the pipeline is testable. This is NOT a production path.
  - API key IS configured but the call fails (bad key, rate limit, 5xx,
    network error, malformed response) -> retry with backoff, then raise
    LLMError if still failing. We do NOT fall back to fake-looking mock
    notes in this case - a real deployment silently sending made-up notes
    to a student is worse than clearly failing. Callers (app.py) catch
    LLMError and mark the call's notes as "pending" instead.
"""

import os
import time
import requests

PROVIDER = os.getenv("NOTES_LLM_PROVIDER", "featherless")  # "featherless" | "gemini"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")

# Configurable, not hardcoded: Google has retired/renamed Gemini models more
# than once during this build already (gemini-2.0-flash hit a 404 once
# retired). Kept configurable via env var in case Gemini is ever switched
# back to, so the next deprecation is a Render dashboard edit, not a code
# push. Default here follows Google's own documented migration target for
# the retired gemini-2.0-flash.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"

MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "1.5"))


class LLMError(Exception):
    pass


def _call_gemini(prompt: str) -> str:
    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except requests.exceptions.RequestException as e:
        # Covers HTTPError (4xx/5xx), ConnectionError, Timeout, etc.
        raise LLMError(f"Gemini request failed: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        # Covers unexpected/empty response shape (e.g. blocked-content
        # responses with no "candidates") and bad JSON.
        raise LLMError(f"Gemini returned an unexpected response: {e}") from e


def _call_featherless(prompt: str) -> str:
    try:
        resp = requests.post(
            FEATHERLESS_URL,
            headers={"Authorization": f"Bearer {FEATHERLESS_API_KEY}"},
            json={
                "model": os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        if not resp.ok:
            raise LLMError(
                f"Featherless HTTP {resp.status_code}: {resp.text[:2000]}"
            )
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        raise LLMError(f"Featherless request failed: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"Featherless returned an unexpected response: {e}") from e


def _call_mock(prompt: str) -> str:
    """Used only when no API key is set, so Day 1 logic is testable offline."""
    return (
        "Newton's Third Law\n\n"
        "Key Point:\n"
        "Har action ka ek barabar aur ulta reaction hota hai.\n\n"
        "Important:\n"
        "- Forces hamesha pair mein aate hain.\n"
        "- Dono forces opposite direction mein lagti hain.\n\n"
        "Example:\n"
        "Jab tum deewar ko dhakka dete ho, deewar bhi tumhe wapas dhakka deti hai."
    )


def call_llm(prompt: str) -> str:
    provider_is_featherless = PROVIDER == "featherless"
    call_fn = _call_featherless if provider_is_featherless else _call_gemini
    api_key_set = bool(FEATHERLESS_API_KEY if provider_is_featherless else GEMINI_API_KEY)

    if not api_key_set:
        print(f"[llm.py] No {PROVIDER} API key configured - using offline mock notes (dev only).")
        return _call_mock(prompt)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return call_fn(prompt)
        except LLMError as e:
            last_error = e
            print(f"[llm.py] {PROVIDER} attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # A real key is configured and every retry still failed - don't paper
    # over this with fake notes. Let the caller decide what to do (app.py
    # marks the call's notes as "pending" and stores the transcript so
    # nothing is lost).
    raise LLMError(f"{PROVIDER} failed after {MAX_RETRIES} attempts: {last_error}")
