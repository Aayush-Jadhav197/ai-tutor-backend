Who Is Vani For?

Vani is designed around learners who can be excluded by conventional digital learning interfaces.

📱 People without smartphones

A basic phone can still provide a way to access a conversational learning experience.

🗣️ People facing language barriers

Learners can communicate naturally rather than being forced into a complicated English-first interface.

🧑‍💻 People with limited technical knowledge

The learner does not need to understand how AI tools, prompts, or applications work.

They simply call and ask.

🌐 People with limited mobile data

The learner's primary interaction happens through a phone call rather than a bandwidth-heavy learning application.

📵 People avoiding smartphone distractions

Some people intentionally use basic phones or reduce smartphone usage to avoid social media and other distractions.

Vani explores how they can still access AI-assisted learning without having to return to a smartphone-based interface.









A Simple Example

Imagine someone wants to understand a topic but does not have access to a smartphone-based AI tutor.

They call Vani.

Learner:

"Mujhe English mein present perfect tense samajh nahi aa raha."

Vani:

Explains the concept conversationally.

Learner:

"Ek example aur batao."

Vani:

Provides another explanation and example.

The learning session then becomes structured revision material:
Phone conversation
        ↓
Transcript
        ↓
AI processing
        ↓
Revision notes
The learner can later open the notes webpage to review what they learned.







Current Prototype

The current prototype demonstrates the complete phone-to-learning workflow.

1. Voice interaction

Vani handles the phone conversation and provides the voice interface for the learner.

2. Post-call webhook

After a call is completed and analyzed, Vani sends a call.analyzed webhook containing the conversation transcript and call information.

3. Transcript processing

The Flask backend receives the webhook, extracts the transcript, and associates the conversation with the learner's phone number.

4. AI-generated notes

The transcript is processed into structured revision notes.

The current prototype uses Featherless for the post-call notes-generation step.

5. Notes retrieval

The generated notes are stored against the learner's phone number and served through a simple notes webpage.

The learner can enter their phone number to retrieve the latest notes.







Architecture:
                ┌──────────────────┐
                │     Learner      │
                │                  │
                │    Phone Call    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │       Vani       │
                │  Voice Assistant │
                └────────┬─────────┘
                         │
                  call.analyzed
                         │
                         ▼
                ┌──────────────────┐
                │  Flask Backend   │
                │                  │
                │ Webhook Handler  │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │    Transcript    │
                │    Processing    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ AI Notes         │
                │ Generation       │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │     Storage      │
                │                  │
                │ Phone → Notes    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │   Notes Page     │
                │                  │
                │ Phone Number →   │
                │ Revision Notes   │
                └──────────────────┘








Technology Stack
Python
Flask
Vani voice agent
Webhook-based call processing
Featherless AI for post-call notes generation
HTML/CSS for the notes interface
Render for backend deployment
GitHub for source control






Repository Structure:
.
├── app.py
├── llm.py
├── notes.py
├── store.py
├── sms.py
├── templates/
│   └── notes.html
├── mock_data/
├── data.json
├── requirements.txt
├── render.yaml
├── Procfile
├── test_day1.py
├── test_integration.py
├── vani_system_prompt.txt
└── README.md




Why a Phone Call?

The phone is already a familiar interface.

For someone who cannot or does not want to use a smartphone application, asking them to download another app is another barrier.

A phone call removes much of that interface complexity.

The learner does not need to learn how to use an AI application before they can start learning.

The call itself becomes the learning interface.







Design Philosophy

Vani is built around one principle:

Do not make the learner adapt to the technology. Adapt the learning interface to the learner.

The prototype therefore prioritizes:

Familiar interaction
Voice-first learning
Language flexibility
Minimal technical complexity
Reduced dependence on smartphone interfaces
Simple post-call revision







Limitations

This is a prototype.

The current implementation demonstrates the core phone-to-learning workflow, but several areas would need further development for a production system:

Broader language support
More robust learner identity and privacy controls
Persistent learning history
Better personalization
More advanced educational evaluation
Production-scale storage
Stronger authentication for notes access
Improved accessibility and reliability testing
More extensive testing across network conditions and different phone types





Future Direction

The long-term vision is not simply to build another AI tutor.

It is to make learning available through interfaces that people already have access to.

Future versions could support:

Multiple regional languages
Personalized learning paths
Progress tracking
Voice-based quizzes
Spoken revision
Offline-friendly learning workflows
Basic-phone learning experiences
Educational support for people with limited digital literacy








The Bigger Idea

AI has the potential to make high-quality explanations available to almost anyone.

But access to the AI itself can still become a barrier.

Vani explores a different question:

What if we stop asking people to get the technology required for learning, and instead bring learning to the technology they already have?

That is the problem Vani is trying to solve.







Status

Working prototype

The current implementation successfully demonstrates:
Phone Call
    ↓
Vani Conversation
    ↓
call.analyzed Webhook
    ↓
Transcript Extraction
    ↓
AI Notes Generation
    ↓
Notes Storage
    ↓
Notes Retrieval



Project Goal

Build something that makes learning accessible to someone who is currently locked out of it.

Vani is our attempt to do exactly that.