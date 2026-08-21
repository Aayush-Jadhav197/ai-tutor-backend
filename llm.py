"""
Notes-generation LLM client.

Vani's own dashboard doesn't list Featherless as an LLM provider, so the LIVE
tutor conversation runs inside Vani on its built-in model. This module is NOT
for the live conversation.

This module is used for the ONE thing that still needs our own LLM call:
turning a finished call transcript into short structured revision notes,
after Vani sends us the call.analyzed webhook.

Default provider: Featherless.

Gemini support is still available if NOTES_LLM_PROVIDER is changed to
"gemini".

Important behavior:
  - No API key configured -> use the canned mock notes for local/offline
    development.
  - API key configured but request fails -> retry with backoff and then raise
    LLMError.
  - We do NOT silently replace failed real LLM responses with fake notes.
"""

import os
import time

import requests


PROVIDER = os.getenv(
    "NOTES_LLM_PROVIDER",
    "featherless"
)  # "featherless" | "gemini"


GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)


FEATHERLESS_API_KEY = os.getenv(
    "FEATHERLESS_API_KEY",
    ""
)


GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash-lite"
)


GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


FEATHERLESS_URL = (
    "https://api.featherless.ai/v1/chat/completions"
)


MAX_RETRIES = int(
    os.getenv(
        "LLM_MAX_RETRIES",
        "3"
    )
)


RETRY_BACKOFF_SECONDS = float(
    os.getenv(
        "LLM_RETRY_BACKOFF_SECONDS",
        "1.5"
    )
)


class LLMError(Exception):
    pass


def _call_gemini(prompt: str) -> str:
    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            },
            timeout=20,
        )

        resp.raise_for_status()

        data = resp.json()

        return (
            data["candidates"][0]
            ["content"]["parts"][0]["text"]
            .strip()
        )

    except requests.exceptions.RequestException as e:
        raise LLMError(
            f"Gemini request failed: {e}"
        ) from e

    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(
            f"Gemini returned an unexpected response: {e}"
        ) from e


def _call_featherless(prompt: str) -> str:
    try:
        resp = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": (
                    f"Bearer {FEATHERLESS_API_KEY}"
                )
            },
            json={
                "model": os.getenv(
                    "FEATHERLESS_MODEL",
                    "Qwen/Qwen2.5-7B-Instruct"
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
            },
            timeout=20,
        )

        resp.raise_for_status()

        data = resp.json()

        return (
            data["choices"][0]
            ["message"]["content"]
            .strip()
        )

    except requests.exceptions.RequestException as e:
        raise LLMError(
            f"Featherless request failed: {e}"
        ) from e

    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(
            f"Featherless returned an unexpected response: {e}"
        ) from e


def _call_mock(prompt: str) -> str:
    """
    Used only when no API key is configured, so local/day-one
    testing can run without an external LLM.
    """

    return (
        "Newton's Third Law\n\n"
        "Key Point:\n"
        "For every action, there is an equal and opposite reaction.\n\n"
        "Important:\n"
        "- Forces always occur in pairs.\n"
        "- The two forces act in opposite directions.\n\n"
        "Example:\n"
        "When you push against a wall, the wall pushes back on you."
    )


def call_llm(prompt: str) -> str:
    """
    Send the notes-generation prompt to the configured LLM provider.
    """

    provider_is_featherless = (
        PROVIDER == "featherless"
    )

    call_fn = (
        _call_featherless
        if provider_is_featherless
        else _call_gemini
    )

    api_key_set = bool(
        FEATHERLESS_API_KEY
        if provider_is_featherless
        else GEMINI_API_KEY
    )

    if not api_key_set:
        print(
            f"[llm.py] No {PROVIDER} API key configured - "
            "using offline mock notes (dev only)."
        )

        return _call_mock(prompt)

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):
        try:
            return call_fn(prompt)

        except LLMError as e:
            last_error = e

            print(
                f"[llm.py] {PROVIDER} "
                f"attempt {attempt}/{MAX_RETRIES} failed: {e}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_BACKOFF_SECONDS * attempt
                )

    raise LLMError(
        f"{PROVIDER} failed after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )
