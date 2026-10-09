# vani-audit

Hackathon PS-01 "Quality Audit & Fix Loop" for IndiaMART's buyer-side VANI voice bot. The pipeline:

1. Transcribe all 713 call recordings.
2. Grade every call Fatal / Non-Fatal, with evidence (quote + timestamp).
3. Flag risky calls.
4. Group failures into ranked root causes.
5. Propose prompt diffs and test them with a simulated buyer (before/after failure rate).
6. Show it all on one dashboard.

Full brief: `docs/PROBLEM_STATEMENT.md`. Current state and findings: `docs/PROJECT_STATUS.md`. Data notes: `docs/DATA.md`.

## My role (Person 1)

Transcription → signals → grading → agreement with human labels. Concretely:

- Bulk STT of all recordings, then normalisation into clean transcripts.
- Deterministic signals: dead air, loops / ask counts, short calls, human-request and frustration cues.
- The LLM grader (Fatal / Non-Fatal plus rubric issues with evidence).
- Hand-labelled subset in `data/labels/` and the agreement metric. This is 25% of the score, so prioritise grade accuracy.

Root-cause clustering, fixes, fix testing and the dashboard are downstream. They consume my outputs, so **keep the output contracts below stable**.

## Repo layout

```
.env                      SARVAM_API_KEY (git-ignored)
requirements.txt          sarvamai, python-dotenv, mutagen
data/                     git-ignored: audio and buyer personal data
  recordings/*.mp3        713 calls, 830.9 min (median 64 s; 74 are <10 s)
  transcripts/raw/        Sarvam STT output, one JSON per call
  transcripts/clean/      normalised transcripts (contract below)
  labels/                 hand-labelled subset (to create)
docs/                     brief, status, rubric (quality_matrix.md), bot prompt (vani_buyer_prompt.md)
prompts/                  our LLM prompts (grader, simulator, fixer)
outputs/                  pipeline results (contracts below)
examples/
```

## Environment gotchas

- **The path has a space:** `/home/imart/Projects/hackathon 2/vani-audit`. Always quote paths in shell commands, and use `pathlib` in Python, never string-splitting on spaces.
- Use the venv: `.venv/bin/python`, `.venv/bin/pip`. Python 3.12.
- **No ffmpeg / ffprobe.** Get audio durations with `mutagen`. Don't plan on audio conversion unless ffmpeg is installed first.
- **Sarvam only.** `SARVAM_API_KEY` is the only key; load it with `python-dotenv`.
  - STT: batch API `client.speech_to_text_job`, with `model="saaras:v4"`, `mode="codemix"`, `language_code="unknown"`, `with_diarization=True`, `num_speakers=2`. Max 20 files per job.
  - Grading: `sarvam-105b` (128K context, a reasoning model, so allow enough `max_tokens`; the Starter cap is 4096).
  - Buyer simulation: `sarvam-105b-conversations` (32K context, tuned for dialogue). `sarvam-30b` is **retired**; the API returns 400.
  - **Rate limit: stay under 40 LLM requests/min**, since limits are per account and shared with teammates. Throttle and retry with backoff.
- **Use the `sarvam-docs` MCP server** (`searchDocs`) for SDK and API details. Don't guess signatures; Sarvam deprecates models often.
- Make batch scripts **resumable and idempotent**: skip calls whose output file already exists. Full runs take a long time and cost credits.
- `docs/` (including the internal bot prompt) is committed to git. Never push to a public remote without excluding it.

## Output contracts

`call_id` = the recording filename stem, e.g. `1000_90643234_F0C4W3KG5` (unique; format `<index>_<id>_<recording_code>`). Every per-call file is named `{call_id}.json`.

**`data/transcripts/raw/{call_id}.json`**: raw Sarvam batch STT output, unmodified. Its entries live in `diarized_transcript.entries[]`, each with `speaker_id`, `transcript`, `start_time_seconds` and `end_time_seconds`.

**`data/transcripts/clean/{call_id}.json`**

```json
{
  "call_id": "1000_90643234_F0C4W3KG5",
  "file": "data/recordings/1000_90643234_F0C4W3KG5.mp3",
  "duration_s": 49.3,
  "language_code": "hi-IN",
  "short_call": false,
  "turns": [
    {"role": "BOT", "text": "...", "start": 0.0, "end": 9.1},
    {"role": "BUYER", "text": "...", "start": 9.3, "end": 10.2}
  ],
  "stats": {"bot_turns": 4, "buyer_turns": 3, "longest_gap_s": 2.5, "gaps_over_5s": 0}
}
```

- `duration_s` comes from mutagen. `short_call` is `duration_s < 10`.
- `role` is BOT or BUYER. Sarvam's `speaker_id` is **not** role-stable (the bot is 0 in some calls and 1 in others), so assign roles by content. The bot says the opening line and mentions IndiaMART / Tania / requirement / seller.
- Gaps are the silence between consecutive turns (`next.start - prev.end`, floored at 0).

**`outputs/signals/{call_id}.json`** and **`outputs/grades/{call_id}.json`**: schemas are defined in the grade step. Document them here once they're fixed.

**`outputs/timeline.json`**: wall-clock timestamps per pipeline stage, used for the "time from issue to tested fix" metric. Each stage name maps to `{"started_at", "finished_at"}` in ISO-8601, plus optional counts (e.g. `"transcribe": {"started_at": "...", "finished_at": "...", "calls": 713}`). Update it at the start and end of each stage, merging rather than overwriting other stages.

## Grading references

- Human rubric: `docs/quality_matrix.md`. The Fatal cell in "Category ISQ's Error" looks inverted; it's pending organiser confirmation.
- Expected bot behaviour: `docs/vani_buyer_prompt.md`. Key rules:
  - ask limits: product 4, qty 3, each spec 2, name / city / state 3, transfer consent 2
  - never ask "any other details?" or "anything else?"
  - languages: Hindi, English, Telugu
  - never claim to be human
  - the End-Call Disposition Table
- Open question: the recordings may come from an older prompt version (see `docs/PROJECT_STATUS.md` §8).
