import os

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM_NUMBER", "")


def send_sms(to_number: str, message: str):
    """
    Sends an SMS via Twilio. If Twilio credentials aren't set (Day 1, no
    keys yet), just prints what would be sent so the pipeline is testable
    end-to-end offline.
    """
    if not (TWILIO_SID and TWILIO_AUTH and TWILIO_FROM):
        print(f"[MOCK SMS] to={to_number}\n{message}\n")
        return {"mock": True}

    from twilio.rest import Client

    client = Client(TWILIO_SID, TWILIO_AUTH)
    return client.messages.create(body=message, from_=TWILIO_FROM, to=to_number)


def build_notes_sms(code: str) -> str:
    return (
        "Your revision notes are ready!\n"
        f"Code: {code}\n"
        "Visit the notes page and enter your phone number + this code to view them."
    )
