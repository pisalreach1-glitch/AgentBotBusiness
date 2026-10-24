# Khmer business agent bot

A local, button-driven Telegram bot with Gemini-powered Planner, QA and
Marketing workflows. Google AI Studio / Gemini Developer API credentials are
used explicitly. The Khmer instructions are in `README_KH.md`.

The complete Antigravity build/audit request is `ANTIGRAVITY_MASTER_PROMPT.txt`.
The code in this directory already implements that reference scope; the prompt
also works as a standalone brief for an empty project.

## Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env locally: TELEGRAM_TOKEN and GEMINI_API_KEY.
.venv/bin/python -m app.main
```

On Windows use `setup_windows.bat`, edit `.env`, then `start_windows.bat`.
Python 3.11+ is required. Only one polling process may use a database at once.

## Verify without credentials or network calls

```bash
python -m compileall -q app tests
python -m unittest discover -s tests -v
```

`TEST_REPORT.txt` records the actual checks on the delivered source.

## Design

Telegram handlers call the application service, which sends a validated source
snapshot to one specialist through the async Google GenAI SDK. SQLite stores
owner/business-scoped profiles, resumable wizard sessions, immutable artifacts,
parent links, active checklists, usage counters and idempotency records.

Handoffs carry explicit source IDs. QA-driven revisions carry both the source
plan and specific QA result. Private-chat checks, owner checks and per-owner
locks guard all callbacks. Partial generations never produce accepted tasks.
Successful generations are saved before delivery; reopening them does not call
Gemini. Failed started requests still count toward the local daily cap.

No external market research or publishing integration is enabled. Input files
are UTF-8 TXT/Markdown only; exports are TXT. Business data used for generation
is sent to Gemini. The local SQLite file is not encrypted. Prompt rules reduce
unsupported claims but do not establish factual correctness of model outputs.

Model availability, provider quotas and free/paid tier conditions depend on the
owner's Google project. No automatic model/provider/billing fallback is used.

Official interfaces checked on 24 October 2026:

- https://ai.google.dev/gemini-api/docs/libraries
- https://googleapis.github.io/python-genai/
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/pricing
- https://docs.python-telegram-bot.org/en/stable/telegram.ext.applicationbuilder.html
