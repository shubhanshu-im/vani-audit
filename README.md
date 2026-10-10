# VANI Quality Audit & Fix Loop

IndiaMART Voice AI Hackathon 2.0, Problem Statement 01. The pipeline audits **every** VANI buyer-side call automatically, finds why calls fail, proposes a prompt fix, and proves the fix works, all on one dashboard.

```
recordings ─► transcribe ─► normalise ─► signals ─► grade ─► root causes ─► rank ─► prompt fix ─► test fix ─► dashboard
 (713 mp3)    Sarvam STT    BOT/BUYER    no-LLM     LLM +     LLM            freq ×    small safe   simulated    Streamlit
                            turns, gaps  checks     evidence  clustering     severity  prompt diff  buyer replay
```

Sarvam only:

| Job | Model |
|---|---|
| Speech-to-text | `saaras:v4` (batch, diarized, code-mix) |
| Grading, root causes, fixes, simulated bot | `sarvam-105b` |
| Simulated buyer | `sarvam-105b-conversations` |
| Voice clips | Bulbul `bulbul:v3` |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "SARVAM_API_KEY=..." > .env
# put the recordings in data/recordings/
.venv/bin/python pipeline/check_setup.py
```

## Run

```bash
.venv/bin/python pipeline/run_all.py              # all 11 steps; every step is resumable
.venv/bin/streamlit run pipeline/dashboard.py     # Audit / Issues / Fix / Test tabs
```

Or run the steps one at a time:

| # | Step | Command | Output |
|---|---|---|---|
| 1 | Transcribe all recordings | `pipeline/transcribe_all.py` | `data/transcripts/raw/` |
| 2 | Turns, BOT/BUYER roles, silences | `pipeline/normalise.py` | `data/transcripts/clean/` |
| 3 | Rule-based signals (loops, ask counts, dead air, frustration, …) | `pipeline/signals.py` | `outputs/signals/` |
| 4 | Grade every call with evidence | `pipeline/grade.py` | `outputs/grades/` |
| 5 | Agreement with human labels | `pipeline/agreement.py` | `outputs/agreement.json` |
| 6 | Root causes | `pipeline/root_causes.py` | `outputs/root_causes.json`, `root_causes_review.md` |
| 7 | Priority = frequency × severity | `pipeline/rank.py` | `outputs/ranking.json` |
| 8 | Prompt fixes + diffs | `pipeline/prompt_fix.py` | `outputs/fixes.json`, `outputs/prompts/`, `prompt_diff*.txt` |
| 9 | Replay calls, old prompt vs new | `pipeline/test_fix.py [C1]` | `outputs/test_results.json`, `outputs/sim/` |
| 10 | Before/after voice clips | `pipeline/voice.py` | `outputs/audio/` |
| 11 | Numbers for the slide | `pipeline/summary.py` | `outputs/results_summary.md` |

**Hosted dashboard.** Recordings, transcripts and per-call outputs are not in git. `pipeline/export_demo_data.py` writes a masked copy of what the dashboard reads (buyer names and phone numbers masked, no audio) to `demo_data/`, which is committed. When `outputs/grades/` is empty, as on Streamlit Community Cloud, the dashboard reads `demo_data/` instead. To deploy, open share.streamlit.io, pick this repo and set the main file to `pipeline/dashboard.py`. Re-run the export after a new grading run.

Two helper scripts:
- **Hand-label set:** `pipeline/make_label_set.py` writes `data/labels/`; the instructions for labellers are in `data/labels/LABELLING_GUIDE.md`.
- **Prompt conversion:** `pipeline/convert_prompt.py` rebuilds `docs/vani_buyer_prompt.md` from the official `.docx`.

## How it works

**Grading.** The grading spec, `prompts/grading_spec.md`, is built from the auditors' Call Quality Matrix and the bot's own rules.
- The LLM judges each of the 17 rubric parameters separately. Each hit comes with a verbatim quote, a timestamp and a root-cause type (PROMPT, ASR, KNOWLEDGE or FLOW).
- **Code decides the grade:** any verified FATAL issue makes the call FATAL.
- Every quote is checked against the transcript. Numbers in the captured lead must appear in the call.
- Calls under 10 s are graded NO_CONVERSATION without an LLM call.

**Risky calls.** Five flags come from the LLM and from rule-based signals:
- wrong requirement
- frustration
- loop
- low ASR confidence
- human request

**Calibration against IndiaMART auditors.** `pipeline/audit_sample.py` takes 160 calls from IndiaMART's own audit sheet, graded by human auditors. We run the pipeline on them (`VANI_DATASET=audit`), tune the grading spec on half, and report agreement on the other half.

**Root causes and ranking (batch learning).** Failed calls are processed in batches. Each batch yields its own patterns, and patterns that recur across batches are merged. **Only causes that pass an importance gate** (type PROMPT, at least 10 calls and at least 1.5% of all calls, seen in at least 3 batches) may change the VANI prompt; smaller ones go on a watchlist.

**Root causes and ranking.** Failed calls are clustered into 5–10 named causes, each typed PROMPT, ASR, KNOWLEDGE or FLOW. VANI is an LLM driven by its prompt, so conversation behaviour (re-asking, closing, language handling) counts as PROMPT. FLOW is kept for problems outside the prompt: telephony, transfer or tool failures, and wrong data passed in. Causes are ranked by **Priority = Frequency × Severity**:
- Frequency is the % of all audited calls in the cause.
- Severity is the average over the cause's calls, with Fatal = 3 and Non-Fatal = 1.

**Fixes.** Each of the top PROMPT-type causes gets one small, behaviour-only change to the real bot prompt. Changes that add numbers, prices or links are rejected. Causes typed ASR, KNOWLEDGE or FLOW are listed as "not fixable by prompt".

**Testing a fix.** For the cause's calls plus some passing calls (to catch regressions), the test does three things:
1. An LLM writes a buyer persona from the real call.
2. That buyer talks to the real bot prompt twice, once as it is now and once with the fix. The prompt template is filled in for each call.
   The simulated buyer **replays the real buyer's lines**, unclear and off-topic ones included, so the replay reproduces what actually confused the bot.
3. Both conversations are graded by the **same grader** as real calls.

It reports the failure rate before and after, a held-out rate (calls not used to write the fix), regressions, and the minutes from root cause found to tested fix.

## Limitations

- Buyers in fix tests are simulated by an LLM. The bot runs as text, without real speech recognition, text-to-speech or tool calls.
- Transcripts come from mono 8 kHz audio, so speaker separation is imperfect. BOT/BUYER roles are corrected line by line from content.
- Rubric parameters that need system data (MCAT, BFD, captured fields) are judged from what the bot said back; otherwise they're `not_assessed`.
- Human labels: 60 calls were hand-labelled by the team (30 for tuning, 30 for testing). Agreement is reported on the test split.
- Samples in fix tests are small, so always read rates together with their n.

## Repo map

| Path | Contents |
|---|---|
| `pipeline/` | All code: the steps above, plus `common.py` (paths, LLM client, rate limit, cache) and `bot_prompt.py` (fills in the prompt template) |
| `prompts/grading_spec.md` | The grader's rubric |
| `docs/` | Problem statement, project status, data notes, Call Quality Matrix, official bot prompt (`.docx` and template `.md`), Person 2's original design brief |
| `data/` (git-ignored) | Recordings, transcripts, labels |
| `outputs/` | Results. The per-call folders are git-ignored because they contain buyer details. |

More detail for developers is in `CLAUDE.md`.
