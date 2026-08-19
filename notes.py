from llm import call_llm

NOTES_PROMPT_TEMPLATE = """You are creating short revision notes from a phone
conversation between a student and an AI tutor. The notes will be sent by SMS
and shown on a simple webpage, so keep them short and easy to read on a small
screen.

Write the notes in the SAME language as the conversation below (Hindi,
Hinglish, or English).

Use exactly this structure:

<Topic Name>

Key Point:
<one sentence>

Important:
- <point 1>
- <point 2>

Example:
<one short real-life example>

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
    prompt = NOTES_PROMPT_TEMPLATE.format(transcript=transcript_text)
    return call_llm(prompt)
