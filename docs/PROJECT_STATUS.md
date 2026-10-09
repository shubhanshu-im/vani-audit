# vani-audit — Project Status (as of 2026-10-09)

Handoff document. The reader is planning the next build steps and writing prompts for Claude Code, which is working in this repo. The full problem statement is in `docs/PROBLEM_STATEMENT.md`.

## 1. What we're building (one paragraph)

The project is Hackathon Problem Statement 01, "Quality Audit & Fix Loop". The pipeline will:

1. Transcribe 100% of the provided VANI voice-bot call recordings.
2. Grade every call Fatal / Non-Fatal, each grade with a reason and evidence (a transcript snippet plus a timestamp).
3. Flag risky calls: wrong requirement, frustration, loops, low ASR confidence, or a request for a human.
4. Group failures into named root causes, typed as prompt / ASR / knowledge / flow, and rank them with a transparent priority score (frequency × severity).
5. Propose a concrete prompt diff for each top root cause.
6. Test each fix by replaying failed scenarios against the revised prompt with a simulated buyer, and report the failure rate before and after.
7. Show all of this on one dashboard.

The scoring weights are: agreement with human auditors 25%, root-cause grouping and ranking 25%, failure-rate drop after a fix 25%, time from issue to tested fix 15%, share of calls audited 10%.

## 2. Status at a glance

| Area | Status |
|---|---|
| Repo + Python env | ✅ Done |
| Data sorted (only what PS-01 needs) | ✅ Done |
| Sarvam speech-to-text (STT) | ✅ Verified on 3 calls (works well on Hindi/Hinglish) |
| Sarvam LLM `sarvam-105b` | ✅ Verified (API reachable with our key) |
| Bot prompt + quality rubric | ✅ In repo, converted to Markdown |
| Transcribe all 713 calls | ⬜ Not started |
| Grading / flagging / root causes / fixes / testing / dashboard | ⬜ Not started (no pipeline code written yet) |
| Human audit labels | ❌ Not provided. We must hand-label a subset (see §7). |
| Git | ⚠️ Repo initialised on branch `master`, **no commits yet** |

## 3. Environment

- Ubuntu Linux, Python 3.12.3, venv at `.venv/`. Install with `.venv/bin/pip install -r requirements.txt`.
- `requirements.txt`: `sarvamai==0.1.36`, `python-dotenv==1.2.4`, `mutagen==1.48.1` (audio durations).
- `.env` holds `SARVAM_API_KEY` (git-ignored). **No other LLM API key is configured yet.**
- **ffmpeg / ffprobe are NOT installed.** Use `mutagen` for durations. If audio needs converting, install ffmpeg first.
- Claude Code has the `sarvam-docs` MCP server connected (Sarvam API docs search). Use it rather than guessing SDK signatures.
- **The project path contains a space**: `/home/imart/Projects/hackathon 2/vani-audit`. Every script and shell command must quote paths.

## 4. Repo layout

```
vani-audit/
├── .env                      # SARVAM_API_KEY (git-ignored)
├── requirements.txt
├── data/                     # git-ignored (audio + personal data)
│   ├── recordings/           # 713 .mp3 call recordings (the dataset)
│   ├── transcripts/raw/      # raw Sarvam STT JSON (3 test calls done)
│   └── labels/               # empty: for our hand-labelled subset
├── docs/
│   ├── PROBLEM_STATEMENT.md  # the hackathon brief
│   ├── PROJECT_STATUS.md     # this file
│   ├── DATA.md               # what each data file is
│   ├── quality_matrix.png    # human auditors' rubric (original image)
│   ├── quality_matrix.md     # same rubric as a table
│   ├── vani_buyer_prompt.docx  # the bot's system prompt (original)
│   └── vani_buyer_prompt.md    # same, as Markdown (~25.5k words, roughly 35–45k tokens)
├── prompts/   (empty)        # for our grader / fixer / simulator LLM prompts
├── outputs/   (empty)        # for pipeline results
└── examples/  (empty)
```

## 5. Data facts

**Recordings (`data/recordings/`)**
- 713 MP3s, 830.9 minutes in total. Median 64 s, p90 130 s, max 387 s.
- **74 calls are under 10 s**: near-empty, dropped, or no-answer calls. Handle them as a separate class; don't send them to the LLM grader.
- Filename pattern: `<index>_<call_id>_<recording_code>.mp3`, e.g. `1000_90643234_F0C4W3KG5.mp3`. Index ranges 2–1001 with gaps. call_id ranges 90643029–90853277.
- **No metadata comes with the recordings.** There are no dispositions, captured lead fields, timestamps or labels. The organisers' `Call_Logs_Hackathon.csv` did not match a single recording by any ID, so it was dropped. The captured lead (product, qty, specs) has to be inferred from the transcript itself, i.e. what the bot said back or confirmed.

**What the calls are:** buyer-side VANI calls. The bot (persona "Tania", female, from IndiaMART) confirms a buyer's product requirement, then collects quantity, specs, name, city and state. Then it either transfers to a live seller or creates a buy lead. The language is mostly Hindi / Hinglish code-mix; some callers speak other languages (e.g. Kannada).

**Dropped as out of scope** (other hackathon tracks): Global Context seller dataset and its data dictionary, Global Context buyer-side API logs, Persona files, Best-Time-to-Call, Seller Enrichment, and the call logs CSV. The originals remain in `~/Downloads/Aids to solve Problems-20261009T085102Z-1-001.zip`.

## 6. What's been verified with Sarvam

**STT, batch API** (`client.speech_to_text_job`):
- Settings used: `model="saaras:v4"`, `mode="codemix"`, `language_code="unknown"`, `with_diarization=True`, `num_speakers=2`.
- Limits: max 20 files per job. Timestamps are per chunk (phrase level), not per word.
- Output JSON has `language_code` and `diarized_transcript.entries[]`, each entry with `speaker_id`, `transcript`, `start_time_seconds` and `end_time_seconds`.
- 3 files took a few minutes end to end. The full run will be about 36 jobs.
- ⚠️ **`speaker_id` is not role-stable.** The bot is `0` in some calls and `1` in others. A role-assignment step is needed. Simple heuristic: the bot is the speaker of the opening line and of lines mentioning "IndiaMART", "Tania", "requirement", "seller".
- Transcription quality on Hinglish product names (e.g. "Hawkins Contura Black 2 litre pressure cooker", "collar type packing machine") looked good.
- The batch output carries no confidence scores. "Low ASR confidence" flags will need a proxy: the bot saying "Sorry, aapki awaaz clear nahi aayi", very short or garbled buyer turns, or the detected language not being Hindi/English.

**LLM:** `client.chat.completions(model="sarvam-105b", ...)` works. It has a 128K context, so the whole bot prompt fits. It is a reasoning model (it spends reasoning tokens, so give it enough `max_tokens`). Rate limit is about 40 requests/min on the Starter plan. **Decided:** `sarvam-105b` for grading, `sarvam-105b-conversations` for buyer simulation (`sarvam-30b` is retired; the API rejects it).

## 7. Grading criteria sources

- **Human rubric** (`docs/quality_matrix.md`): Outcome Accuracy (always fatal), Qty value / unit / probing / missed, Spec1/Spec2 accuracy and missed, Looping (more than 1+2 probes), WER, Additional details (BFD), OnCall AstBuy Error (seller available but not pitched = fatal), Product change / wrong product approved, Category ISQ, MCAT errors, Dead air (>5 s, or >8 s with noise), Overall (Pass / Fatal / Non-Fatal).
  - The "Category ISQ's Error" fatal cell appears inverted in the source; the organisers need to confirm it.
  - Some parameters (MCAT, BFD, captured qty) need system data we don't have. We can only judge them from what the bot says aloud.
- **Bot prompt** (`docs/vani_buyer_prompt.md`): this is the spec for "correct" behaviour. Rules most useful for grading:
  - **Ask limits:** product identification 4, quantity 3, each spec 2, name 3, city 3, state 3, transfer consent 2. Exceeding a limit is a loop.
  - **Banned open-ended questions:** "Any other details?", "Anything else?".
  - **Languages:** English, Hindi or Telugu only.
  - **Never claim to be human.**
  - **End-Call Disposition Table:** e.g. BL Deleted / No Product Requirement / Selling Related / Job Related.
  - The intent catalogue includes TALKING_TO_AI, COMPLAINT, LANGUAGE_SWITCH and CONNECTIVITY_ISSUE.
  - The prompt is a Jinja-style template (195 `{% %}` blocks). It has variables such as `@call_outcome`, `@buyer_disposition`, `@quantity`, `@specifications` and `@connect_ast_call`, and tools such as `end_interaction` and `ast_buy_confirmed_tool`.
- **Hand labels:** none exist yet. `data/labels/` is ready for them. The plan is to hand-label a representative subset (e.g. 50–80 calls, stratified by duration) as Fatal / Non-Fatal plus the rubric parameter, to measure agreement (25% of score).

## 8. Open decisions / risks

1. **LLM choice (decided):** Sarvam only. `sarvam-105b` for grading, `sarvam-105b-conversations` for buyer simulation. Name them on the final slide.
2. **The prompt may not match the calls.**
   - In call `470_…`, the bot told a Kannada speaker it speaks "only Hindi or English", but the prompt says Hindi, English *or Telugu*.
   - In call `1000_…`, the bot asked "kya aap koi aur details share karna chahenge?", which the prompt explicitly bans.
   - So either the recordings came from an older prompt version, or these are genuine violations. Ask the organisers which prompt version produced the calls. It affects both grading and how fixes are framed.
3. **Human labels:** the organisers marked them "to be confirmed". Ask whether they will provide any; otherwise plan time for hand-labelling.
4. **The prompt is long** (~40k tokens). Fix-testing replays the full revised prompt each turn, so cost and latency matter. Keep the test set small and targeted (the failed calls for one root cause plus some control calls).
5. **Audio and transcripts contain buyer names and cities.** Keep them under git-ignored `data/`. Note that `docs/` (including the internal bot prompt) *is* tracked by git, so don't push to a public remote without excluding it.

## 9. Early failure examples (from the 3 probe transcripts)

| Call | Observation | Likely rubric item / root-cause type |
|---|---|---|
| `1000_90643234_F0C4W3KG5` | Bot asks the banned "any other details?" question. The buyer wants 5 sizes (1.5–6.5 L) at 50% off MRP, the bot just says "details note kar li" and ends. Multi-size quantity isn't captured or confirmed. | Spec/Qty missed opportunity; prompt-violation |
| `470_90685058_U6YFJ1UG5` | Buyer speaks Kannada; bot keeps asking quantity and specs, then repeats the language line twice. | Language handling; looping; flow |
| `850_90797398_E2ZUXXFH5` | Bot re-confirms the product 3× despite "haan". When asked for city it captures a name ("Sujit Kumar"), and the city ask repeats. | Looping; ASR/understanding of short replies; wrong field capture |

## 10. Suggested next steps (in order)

1. **Bulk transcription:** a script that runs all 713 recordings through Sarvam batch STT (20 per job, resumable, skips already-done files) into `data/transcripts/`. Then normalise each to a clean JSON (turns with role bot/buyer, text, start/end) plus call-level stats (duration, turn counts, longest silence or gap for dead air).
2. **Hand-label set:** pick a stratified subset of about 60 calls and write a simple labelling sheet (`data/labels/human_labels.csv`: call_id, grade, fatal_reason, rubric_param, notes). Label them early so the grader can be tuned against them.
3. **Grader:**
   - Write an LLM prompt (in `prompts/`) built from the rubric and the key bot-prompt rules.
   - Output strict JSON per call: grade, list of issues (rubric parameter, fatal flag, evidence quote, timestamp, root-cause type), risky-call flags, and the inferred captured lead.
   - Add deterministic checks alongside: dead-air gaps, repeated bot utterances or ask counts for loops, and "speak to a human" keywords.
4. **Root-cause clustering + ranking:**
   - Cluster the issue descriptions (LLM-assisted) into about 8–15 named root causes, each with type, description and example call IDs.
   - Priority = frequency × severity weight, with the formula documented.
5. **Fix + test loop:**
   - For the top root causes, have the LLM propose a prompt diff against `docs/vani_buyer_prompt.md`.
   - Build a simulated buyer from each failed call's transcript, then replay against the original and the revised prompt, grade both, and report before/after failure rates and the time taken.
6. **Dashboard:** a single page (Streamlit or a static HTML page) reading from `outputs/`, with four panels: Audit, Issues, Fixes, Tests.
7. Results summary + one-slide deck at the end.
