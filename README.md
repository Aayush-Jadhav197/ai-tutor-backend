# AI Tutor Backend (Person 2 side)

## Status: Vani → Flask pipeline complete and verified against Vani's real docs

This backend receives Vani's `call.analyzed` webhook, stores the
transcript, generates revision notes via Gemini, sends the SMS with a
retrieval code, and serves a printable notes webpage. Everything below was
checked against the actual 121-page Vani documentation PDF, not assumed.

## What changed vs. the original Twilio-based plan

- The **live** call (STT → LLM → TTS) happens *inside Vani*, on its
  built-in Gemini model. We don't write code for that - `vani_system_prompt.txt`
  gets pasted into the Vani agent config (already done, per your setup).
- Vani sends a **`call.analyzed` webhook** once, at the end of a call, with
  the full transcript in one payload. Vani's own docs recommend this as
  "the single event to subscribe to" - so that's the only event type this
  backend handles.
- SMS still goes through **Twilio**, decoupled from voice - Vani handles
  telephony, Twilio only sends the post-call notes SMS.

## Verified against Vani's actual webhook documentation

Three things in the original code were **wrong** relative to Vani's real
spec, and are now fixed:

1. **Call identity.** The code used the webhook envelope's `id` field as
   the call identifier. Per Vani's docs, envelope `id` is a **per-delivery
   event ID** (changes on retry), not a call ID. The correct, stable
   identifier is `data.conversation_id` ("stable across all events for the
   same call"). Fixed in `resolve_call_id()` in `app.py`.
2. **Signature header name.** Was a placeholder (`X-Vani-Signature`).
   Vani's docs confirm the real header is **`X-Webhook-Signature`**,
   verified as an HMAC-SHA256 hex digest over the **raw** request body.
   Fixed in `app.py` / `.env.example`.
3. **Mock payload shape.** `mock_data/sample_call_analyzed.json` was
   missing fields the real payload always includes (`conversation_id`,
   `call_sid`, `workspace_id`, `qualification_data`, etc.) and had an
   invented `call_id` field that doesn't exist in Vani's schema at all.
   Rewritten to match the real shape from Vani's docs.

## Folder structure

```
ai-tutor-backend/
  app.py                          Flask app: webhook + notes API + printable page
  notes.py                        Builds the notes prompt, calls the LLM
  llm.py                          Gemini/Featherless client, retry logic, offline mock
  store.py                        JSON-file storage (calls, notes, codes, idempotency)
  sms.py                          Twilio SMS + offline mock
  vani_system_prompt.txt          Already pasted into the Vani agent config
  templates/notes.html            Printable "enter phone + code" notes webpage
  mock_data/sample_call_analyzed.json   Real-shaped Vani webhook payload for testing
  test_day1.py                    Original offline pipeline test
  test_integration.py             Reliability tests: signature, idempotency, failure paths
  requirements.txt
  .env.example
  Procfile                        Render start command
  render.yaml                     Optional infra-as-code for Render
```

## Setup

```bash
cd ai-tutor-backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in GEMINI_API_KEY (or leave blank to use the offline mock) and
# TWILIO_* (or leave blank to just print the SMS to console)
```

## Run the tests offline (no Vani, no Twilio, no API keys needed)

```bash
python3 test_day1.py          # original pipeline: notes -> storage -> SMS -> retrieval
python3 test_integration.py   # reliability: signature verification, duplicate webhook
                               # delivery, empty transcript, missing phone, LLM failure
```

`test_integration.py` specifically proves:
- `conversation_id` (not the envelope's event `id`) is used as the call identifier
- a bad `X-Webhook-Signature` is rejected with 401, a correct one is accepted
- the same event ID delivered twice (Vani retries non-2xx deliveries with
  backoff) is recognized as a duplicate and not double-processed
- an empty transcript doesn't crash the handler
- a missing phone number skips the SMS step but still generates notes
- a real Gemini failure (key configured, call still fails) marks notes as
  **pending** instead of silently sending fabricated content
- the printable notes page renders and round-trips through `/notes`

## Run the actual server locally

```bash
python3 app.py
```

Then simulate a Vani webhook hitting it:

```bash
curl -X POST http://localhost:5000/webhook/vani \
  -H "Content-Type: application/json" \
  -d @mock_data/sample_call_analyzed.json
```

Open the printable notes page in a browser: `http://localhost:5000/`
(enter the phone number + code printed in the server logs).

## Webhook signature verification (confirmed against Vani's docs)

Off by default (`WEBHOOK_VERIFY_SIGNATURE=false`) so local testing needs no
secret. Confirmed spec, straight from Vani's "Webhooks & Events" doc page:

- Header: `X-Webhook-Signature`
- Algorithm: HMAC-SHA256, hex digest
- Signed over the **raw** request body (not re-serialized JSON)
- Key: your webhook subscription secret from Vani's dashboard

Before the real demo: set `WEBHOOK_VERIFY_SIGNATURE=true` and
`VANI_WEBHOOK_SECRET=<value from Vani>` (see manual steps below for where
to get that secret).

## Reliability / error handling

- **Idempotency:** Vani retries webhook deliveries that don't return 2xx
  within 5 seconds, with exponential backoff up to ~30s. The same event
  `id` can arrive more than once - `store.py` tracks processed event IDs
  so a retried delivery is a no-op, not a duplicate SMS.
- **Fast response, async processing:** per Vani's own best-practice
  ("respond within 5 seconds, queue work asynchronously"), the webhook
  route validates + responds immediately, then does the actual notes
  generation + SMS send in a background thread.
- **Empty transcript:** stored and flagged (`status: empty_transcript`),
  not passed to the LLM.
- **Missing phone number:** notes are still generated and stored; only the
  SMS step is skipped (`sms_status: skipped_no_phone`).
- **LLM failure:** `llm.py` retries (3 attempts, exponential backoff) only
  when a real API key is configured. If it still fails, the transcript is
  saved and the call is marked `notes_status: pending` with the error
  message - **notes are never silently fabricated** in that case. (The
  offline mock fallback only fires when no API key is configured at all -
  i.e. local/dev testing, not a production failure.)
- **SMS failure:** caught separately from notes generation - if Twilio is
  down, the notes + retrieval code are still saved and accessible via the
  webpage even though the SMS didn't go out.

## Deploying to Render

```bash
cd ai-tutor-backend
git init && git add . && git commit -m "Vani integration + reliability fixes"
git push <your-render-connected-repo> main
```

On Render: New → Web Service → connect the repo. `Procfile` and
`render.yaml` should let Render auto-detect the build/start commands
(`pip install -r requirements.txt` / `gunicorn app:app --bind 0.0.0.0:$PORT`),
but double-check them in the dashboard on first deploy. Then set these
environment variables under the service's "Environment" tab (not
committed to git):

- `GEMINI_API_KEY`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- `WEBHOOK_VERIFY_SIGNATURE=true` and `VANI_WEBHOOK_SECRET=<from Vani>`

Once deployed, give Person 1 the Render URL + `/webhook/vani` to paste
into Vani's webhook config.

Note: this was built and tested locally (gunicorn boots, `/health` and
`/webhook/vani` both verified against the real payload shape) - actually
deploying to your Render account requires your login, so that step is
still yours to do (see below).

---

## What YOU still need to do manually

These require access to your accounts/dashboard/phone - not something
that can be done from here.

1. **Get the webhook secret from Vani.** In the Vani dashboard, go to
   Settings → Webhooks → Add Webhook, enter your Render URL +
   `/webhook/vani` (must be HTTPS), select the `call.analyzed` event, and
   save. Vani should show/generate a signing secret at that point - copy
   it into `VANI_WEBHOOK_SECRET` on Render. (The docs describe the
   verification mechanism in detail but the exact in-dashboard flow for
   generating that first secret wasn't visible in the screenshots
   available - if the UI looks different than expected, it'll be right
   there on the same Settings → Webhooks screen.)

2. **Deploy to Render.** Connect your GitHub repo, set the environment
   variables listed above, deploy, and test `/health`.

3. **Register the webhook URL in Vani** once you have the Render URL.

4. **Real phone testing** - call your Vani number and test:
   - Hindi: "Newton ka third law samjhao"
   - Follow-up: "Ek aur example do"
   - Hinglish: "Photosynthesis simple language mein samjhao"
   - Unclear speech, to see how Vani's STT + your tutor prompt handle it
   
   Then confirm: call ends → SMS arrives → code works on the notes page.

5. **Verify Twilio SMS** - if keeping Twilio for SMS only, confirm your
   Twilio account/number can actually send to your test phone (trial
   accounts often require verifying the destination number first).

6. **Final demo run-through:** keypad phone → call Vani number → ask
   question → follow-up → hang up → receive SMS → open notes page → enter
   code → print.

## Explicitly not done (out of scope per the plan)

- Replacing Gemini with Featherless (not in Vani's LLM list; Gemini is the
  hackathon default per your own priority order)
- Any change to Vani's STT/TTS/voice config - your live call is already
  working, untouched here
- A real database (JSON file + lock is sufficient for hackathon scale)
