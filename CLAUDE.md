# vani-audit

Hackathon PS-01 "Quality Audit & Fix Loop" for IndiaMART's buyer-side VANI voice bot. The pipeline:

1. Transcribe all 713 call recordings.
2. Grade every call Fatal / Non-Fatal, with evidence (quote + timestamp).
3. Flag risky calls.
4. Group failures into ranked root causes.
5. Propose prompt diffs and test them with a simulated buyer (before/after failure rate).
6. Show it all on one dashboard.

Full brief: `docs/PROBLEM_STATEMENT.md`. Current state and findings: `docs/PROJECT_STATUS.md`. Data notes: `docs/DATA.md`.

## Team and code ownership

All code lives in ONE package, `pipeline/`, run in order by `pipeline/run_all.py` (see `README.md`).

| Area | Owner | Scripts |
|---|---|---|
| Audit | Person 1 | transcribe_all, normalise, signals, grade, make_label_set, agreement |
| Issue / Fix / Test | Person 2 (merged in from github.com/SomilMathur1411/person2-analysis; original design in `docs/person2_build_brief.md`) | root_causes, rank, prompt_fix, test_fix, voice, summary, dashboard |
| Labels | Person 3 | data/labels/human_labels.csv |

Agreement with human labels is 25% of the score, so prioritise grade accuracy. Later steps read earlier steps' files, so **keep the output contracts below stable**. Shared code is in `common.py`: paths, atomic JSON writes, `timeline_update()`, `RateLimiter`, and the LLM helpers `chat` / `llm` / `llm_json` / `parallel_map`. `bot_prompt.py` renders the bot prompt template.

## Repo layout

```
.env                      SARVAM_API_KEY (git-ignored)
requirements.txt          sarvamai, python-dotenv, mutagen, pydantic, jinja2, streamlit, pandas
README.md                 team-facing overview, setup, run commands, method, limitations
data/                     git-ignored: audio and buyer personal data
  recordings/*.mp3        713 calls, 830.9 min (median 64 s; 74 are <10 s)
  transcripts/raw/        Sarvam STT output, one JSON per call
  transcripts/clean/      normalised transcripts (contract below)
  labels/                 60-call hand-label set: human_labels.csv, review/*.txt, LABELLING_GUIDE.md
docs/                     brief, status, data notes, rubric (quality_matrix.md), Person 2's design brief,
                          official bot prompt: vani_buyer_prompt.docx (source) + .md (Jinja template)
prompts/grading_spec.md   the grader's rubric
pipeline/                 all code (one package; see the table above)
outputs/                  results (contracts below); per-call dirs are git-ignored (buyer PII)
  prompts/                generated fixed prompts vani_prompt_v2[_<cause>].md
_incoming/                git-ignored clones of teammates' repos (reference only)
```

## Datasets: our calls vs the human-audited calls

`VANI_DATASET` (default `main`) picks the calls a run works on, through the paths in `common.py`.
- `main`: our 713 hackathon recordings, in `data/` and `outputs/`.
- `audit`: a calibration sample from IndiaMART's real audit sheet, in `data/audit/` and `outputs/audit/`. The sheet (`data/audit/audit_source_of_truth.csv`, from "Audit Source of truth.csv") has about 4,900 VANI calls graded by IndiaMART auditors, with a Pass / Non-Fatal / Fatal / NA verdict for each rubric parameter, remarks, and Call / BL / Overall Quality.
  - None of these calls are in our 713. The SQUADSTACK recordings download publicly; the Sarvam-hosted ones need a login.
  - `pipeline/audit_sample.py` downloads a stratified 160 (54 Fatal / 53 Non-Fatal / 53 Pass by Overall Quality, half tune / half test) and writes `data/audit/labels.csv`.
  - Run every step with `VANI_DATASET=audit …`. Then `agreement.py` reports binary, 3-class and population-weighted agreement, plus per-parameter agreement.
  - Human base rates for these inbound calls: Overall 69% Pass / 19% Non-Fatal / 12% Fatal. Call Quality Fatal is 3.5%; Outcome Accuracy Fatal is 2.8%.
- The LLM cache (`outputs/.cache`) and fixed prompts (`outputs/prompts`) are shared by both datasets.

## Environment gotchas

- **The path has a space:** `/home/imart/Projects/hackathon 2/vani-audit`. Always quote paths in shell commands, and use `pathlib` in Python, never string-splitting on spaces.
- Use the venv: `.venv/bin/python`, `.venv/bin/pip`. Python 3.12.
- **No ffmpeg / ffprobe.** Get audio durations with `mutagen`. Don't plan on audio conversion unless ffmpeg is installed first.
- **Sarvam only.** `SARVAM_API_KEY` is the only key; load it with `python-dotenv`.
  - STT: batch API `client.speech_to_text_job`, with `model="saaras:v4"`, `mode="codemix"`, `language_code="unknown"`, `with_diarization=True`, `num_speakers=2`. Max 20 files per job.
  - LLM: always go through `common.chat()` / `llm()` / `llm_json()`. They send `reasoning_effort=None`, share one 35 req/min limiter per process, retry with backoff, and cache temperature-0 calls in `outputs/.cache/`.
  - `sarvam-105b` (`MODEL_MAIN`, 128K context): grading, root causes, fixes, and the simulated BOT (the rendered prompt is about 25-35k tokens).
  - `sarvam-105b-conversations` (`MODEL_CHAT`, 32K context): the simulated BUYER.
  - `sarvam-30b` is **retired**; the API returns 400.
  - TTS: `client.text_to_speech.convert(text, language_code, model="bulbul:v3", speaker="tanya"|"rahul")` returns base64 WAV in `.audios[0]`, 22050 Hz mono.
  - **Credits:** on 2026-10-10 at 01:51 the account hit HTTP 402 "No credits available", which blocks every Sarvam service. Fix-test simulations are the most expensive step: each bot turn sends the whole rendered prompt (about 30k tokens).
- **Rate limit: 40 LLM requests/min per account, shared with teammates.** Each process limits itself to 35, so **don't run two LLM-heavy scripts at once** (e.g. grade + test_fix).
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
- Gaps are measured on the raw STT chunks before merging, as time when **nobody** is speaking (from the latest end time seen so far). Chunks overlap often, so a naive `next.start - prev.end` invents dead air.
- Extra fields beyond the core contract:
  - `stats.long_gaps`: `[{start, end, duration_s, after_role}]` for gaps over 5 s, which serve as dead-air evidence with timestamps.
  - `role_assignment`: `{bot_speaker_id, lines_reassigned_by_content, cue_hits_by_speaker, ambiguous, reasons}`.
- Roles are assigned in two passes. First the bot speaker is picked per `speaker_id`. Then each chunk is re-labelled by content (`BOT_LINE` / `BUYER_LINE` patterns in `normalise.py`), and chunks that merge both voices are split by sentence. Diarization of the mono audio often mixes both people under one `speaker_id`; about 300 of 639 calls get at least one line corrected.
- Known STT caveat: the audio is mono 8 kHz, so diarization sometimes puts fragments of the bot's speech into BUYER turns (echoes like "packaging size के बारे में बात कर"). Turns can also overlap in time. The grader must not read an echoed fragment as the buyer repeating themselves.

## Pipeline commands

```bash
.venv/bin/python pipeline/check_setup.py      # what is ready / missing (--no-api to skip live calls)
.venv/bin/python pipeline/run_all.py          # steps 1-11 in order; --from N --to N --cause C2 --skip-voice
.venv/bin/streamlit run pipeline/dashboard.py
# single steps: transcribe_all, normalise, signals (--force), grade (--files/--limit/--force/--workers),
# agreement, root_causes, rank, prompt_fix (--top/--cause), test_fix ([C1] --max-target --n-reg --turns --combined),
# voice, summary; helpers: make_label_set, convert_prompt, audit_sample, export_cowork,
# export_demo_data (masked copy of the dashboard's inputs in demo_data/, used when outputs/grades/ is empty)
```

**`data/labels/human_labels.csv`** (UTF-8 with BOM, so read it with `encoding="utf-8-sig"`). Columns:
- `call_id`
- `split`: tune / test
- `grade`: FATAL / NON_FATAL
- `rubric_params`: `;`-separated, names exactly as in `docs/quality_matrix.md`
- `fatal_reason`, `notes`, `labelled_by`

The 60 calls exclude short calls. They are drawn equally from 4 duration-quantile bins with seed 20261009, and paired by duration into 30 tune / 30 test. Tune the grader on `tune` only; report agreement on `test`.

The selection is recorded in `data/labels/label_set.json`. Review transcripts are in `data/labels/review/`, and the labeller instructions in `data/labels/LABELLING_GUIDE.md`. The script refuses to overwrite a CSV that already has labels.

Code lives in `pipeline/`. Shared paths, atomic JSON writes and `timeline_update()` are in `pipeline/common.py`. Per-file STT failures go to `data/transcripts/errors.json`.

**`outputs/signals/{call_id}.json`** (from `pipeline/signals.py`, no LLM): `{call_id, version, short_call, duration_s, signals}`.

- Each signal is `{detected, count, evidence: [{t, role, quote}], ...extras}`.
- `t` is the start of the turn containing the quote.
- The signals:
  - `loop`: has `fields: [{field, asks, limit}]`
  - `ask_counts`: `{field: {count, limit}}`, with spec fields keyed `spec:<name>`
  - `bot_repetition`: has `clusters`
  - `buyer_repetition`
  - `echo_fragments`: informational only
  - `dead_air`: has `gaps` with `bot_attributable` and `threshold_s` (5 s, or 8 s near "awaaz clear nahi")
  - `human_request`
  - `ai_identity_question`: has `bot_disclosed_ai`
  - `frustration`
  - `banned_question`
  - `bot_claims_human`
  - `language_list_omits_telugu`
  - `answer_field_mismatch`
  - `low_asr`: has sub-signals `bot_not_understood`, `short_garbled_buyer_turns` and `other_language`
  - `abrupt_end`: no closing line; has `ending_hint`
- Ask counting: at most one ask per field per BOT turn. Ask limits are in `ASK_LIMITS`.

**`outputs/grades/{call_id}.json`** (from `pipeline/grade.py`, `sarvam-105b`, temperature 0, reasoning off):

```
{call_id, duration_s, graded_at, spec_hash, model, attempts, usage, unresolved_problems,
 grade: FATAL | NON_FATAL | NO_CONVERSATION, grade_reason, fatal_params: [param],
 issues: [ParamResult with hit=true],
 parameters: [ParamResult x 17],     # one per rubric param in docs/quality_matrix.md
 captured_lead: {product, quantity, quantity_unit, specs: [{name, value}], name, city, state},
 risk_flags: [{flag: wrong_requirement|frustration|loop|low_confidence|human_request, reason, timestamp, source: llm|signals}],
 analysis, failure_description, confidence: high|medium|low, needs_review}
ParamResult = {param, verdict: PASS|NON_FATAL|FATAL|NA, hit, assessable, severity, evidence_quote, timestamp,
               root_cause_type: PROMPT|ASR|KNOWLEDGE|FLOW, confidence, reason, evidence_verified, llm_timestamp?, llm_severity?}
grade also has quality: PASS|NON_FATAL|FATAL|NA (the auditors' Overall Quality scale)
```

How grades are derived and checked:
- The spec is v2 (`prompts/grading_spec.md`), calibrated on the audit sheet; v1 is kept as `grading_spec_v1.md`. `grade.py --spec <file>` picks another spec.
- The LLM gives each parameter an auditor-style `verdict`. `hit`, `severity` and `assessable` are derived from it.
- Code derives `grade`: any verified FATAL → FATAL. `quality` is FATAL > NON_FATAL > PASS. `FORCED_SEVERITY` only forces Outcome Accuracy to FATAL.
- Short calls get NO_CONVERSATION without an LLM call.
- Quotes are verified against the transcript, and `timestamp` is replaced by the start of the matched turn.
- Failures go to `outputs/grades/errors.json`.
- `spec_hash` changes whenever `prompts/grading_spec.md` changes. Regrade with `--force`.

**Sarvam LLM gotcha:** `sarvam-105b` reasoning, even at `reasoning_effort="low"`, can use the entire 4096-token Starter `max_tokens` and return empty content. Pass `reasoning_effort=None` explicitly; leaving it out means maximum reasoning. The grader gets a bounded `analysis` field to think in instead.

**`outputs/timeline.json`**: wall-clock timestamps per stage, written with `timeline_update()`, which merges into the stage's entry and never overwrites other stages.
- Each stage name maps to `{started_at, finished_at, status, ...counts}` in ISO-8601.
- The stages are: transcribe, normalise, signals, grade, agreement, root_causes, rank, prompt_fix, test_fix, voice.
- "Time from issue to tested fix" = `minutes_between("root_causes", "test_fix")`.

**Issue / fix / test outputs:**
- `outputs/agreement.json`: `{agreement_pct, kappa, headline_split, n_labelled, overall, by_split: {tune, test}, disagreements}`. Agreement and kappa are null until labels exist.
- `outputs/root_causes.json` uses **batch learning**: failed calls are cut into batches of 40, the LLM extracts patterns per batch (map), and patterns are merged into causes (reduce). The file holds `{method, batch_size, n_batches, n_audited, n_failed, causes: [{id, name, description, type, patterns, n_calls, fatal_calls, n_batches}], patterns, assignments: {call_id: cause_id|"Other"}, call_causes}`.
- `outputs/ranking.json` adds the **importance gate**: `updates_prompt` / `gate_reason` per cause. A cause passes only if it is type PROMPT, has at least 10 calls and at least 1.5% of audited calls, and appeared in at least 3 batches. `prompt_fix.py` only fixes gated causes; the others go to `fixes.json` `watchlist`. A call is "failed" if it is FATAL or has any verified issue (`common.is_failed`). Hand overrides go in `outputs/root_causes_overrides.json`.
- `outputs/ranking.json`: `{formula, total_calls, ranking: [{...cause, n_calls, fatal_calls, frequency_pct, severity, priority, examples, rank}]}`.
- `outputs/fixes.json`: `{fixes: [{cause_id, action, where, old_text, new_text, why, examples, applied_as, created_at}], not_fixable_by_prompt}`. Prompts go to `outputs/prompts/`, diffs to `outputs/prompt_diff[_<cause>].txt`.
- `outputs/test_results.json`: `{summary: {...before/after rates, held-out, regression, minutes}, results: [per call: persona, context, before/after {turns, grade, reason, shows_cause}]}`. Per-call cache is in `outputs/sim/`.
- **Fix-test simulation** (`test_fix.py`):
  - The buyer (`MODEL_CHAT`) gets a persona plus the real buyer lines (`buyer_script`) and reuses them in order. Without the real lines, simulated buyers are clean and cooperative and never trigger the real failures.
  - The buyer's `[END]` is only honoured after the bot closed or transferred (`BOT_CLOSED`); the model adds `[END]` spuriously.
  - Bot actions are written as `ACTION:` lines and stripped by `bot_prompt.split_actions`.
- `prompt_fix.py` asks for the existing rule that should have prevented the cause, and puts the change in the section that governs that behaviour. `--cause` accepts any cause type.
- `docs/vani_buyer_prompt.md` is a **Jinja template**: `{{ var }}` placeholders and `{% if %}` blocks, with `@var` marking variables the bot sets. Fixes edit this file's text, and `bot_prompt.render()` fills it per call for simulation. Regenerate it from the `.docx` with `convert_prompt.py`.

## Cowork + Voice Agents MCP (prompt update and live demo)

1. Our pipeline does the measuring.
2. `pipeline/export_cowork.py` writes `outputs/cowork/` (git-ignored; names and phones masked):
   - for the Cowork chat: `00_INSTRUCTIONS.md`, `01_summary.md`, `02_proposed_changes.md`
   - for a Cowork knowledge base: `calls.csv`, `batches/batch_NN.md` (same batches as root_causes), `vani_prompt_current.md`, `vani_prompt_updated.md`
3. Cowork reviews the gated changes and outputs the final prompt.
4. The prompt is applied to test voice agents via the **Sarvam Voice Agents MCP**:
   - URL: `https://mcp.sarvam.ai/voice-agents`
   - Auth: OAuth via Sarvam SSO, 24 h token, bound to one org/workspace.
   - Tools: `agents` (list / read / commit) and `configure_agent` (create / update / clone; edits the draft).
   - Its docs list Claude Code / Desktop / Cursor / VS Code as clients, **not Cowork**, so Claude Code is the fallback.
   - Gotchas: lists in an update replace the old list; a prompt that references undefined variables or tools is rejected.
   - Never edit IndiaMART's production agent.
5. Upload to Cowork directly. The GitHub connector exists, but reading private-repo files isn't documented.

## Grading references

- Human rubric: `docs/quality_matrix.md`. The Fatal cell in "Category ISQ's Error" looks inverted; it's pending organiser confirmation.
- Expected bot behaviour: `docs/vani_buyer_prompt.md`. Key rules:
  - ask limits: product 4, qty 3, each spec 2, name / city / state 3, transfer consent 2
  - never ask "any other details?" or "anything else?"
  - languages: Hindi, English, Telugu
  - never claim to be human
  - the End-Call Disposition Table
- Open question: the recordings may come from an older prompt version (see `docs/PROJECT_STATUS.md` §8).
