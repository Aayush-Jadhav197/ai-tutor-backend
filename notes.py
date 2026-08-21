from llm import call_llm


NOTES_PROMPT_TEMPLATE = """You are creating short revision notes from a phone
conversation between a learner and an AI tutor.

The notes will be sent by SMS and shown on a simple webpage, so keep them
short, clear, accurate, and easy to read on a small screen.

CRITICAL LANGUAGE RULE:
- Write ALL notes in ENGLISH ONLY.
- NEVER write notes in Hindi.
- NEVER write notes in Hinglish.
- NEVER use Devanagari script.
- NEVER copy Hindi or Hinglish sentences from the transcript into the notes.
- If the conversation is in Hindi or Hinglish, understand its meaning and
  translate/summarize that meaning into natural English.
- The language used by the learner or tutor in the conversation must NEVER
  determine the language of the notes.
- Topic Name MUST be in English.
- Key Point MUST be in English.
- Every Important point MUST be in English.
- Example MUST be in English.
- Do not include translations in parentheses.
- Do not provide multiple language versions.

Use exactly this structure:

<Topic Name>

Key Point:
<one clear sentence in English>

Important:
- <point 1 in English>
- <point 2 in English>

Example:
<one short real-life example in English>

Conversation transcript:
{transcript}
"""


def format_transcript(messages):
    """messages: list of {"speaker"/"role": "...", "text"/"content": "..."}"""

    lines = []

    for m in messages:
        speaker = m.get("speaker") or m.get("role", "user")
        text = m.get("text") or m.get("content", "")

        label = "Tutor" if speaker == "agent" else "Student"

        lines.append(f"{label}: {text}")

    return "\n".join(lines)


def generate_notes(messages) -> str:
    transcript_text = format_transcript(messages)

    prompt = NOTES_PROMPT_TEMPLATE.format(
        transcript=transcript_text
    )

    return call_llm(prompt)
