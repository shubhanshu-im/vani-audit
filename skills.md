# skills.md: build journey and tools used

**Team project:** PS-01 Quality Audit & Fix Loop for IndiaMART's buyer-side voice bot, VANI.
**What it does:** audits every call, finds and ranks why calls fail, writes a prompt fix, and proves the fix on live Sarvam voice agents.

| | |
|---|---|
| Calls audited | **713 / 713** (100%, no sampling) |
| Agreement with IndiaMART's human auditors | **59.0%** Fatal vs not on held-out calls, **67.3%** weighted to real traffic |
| Root cause fixed | C3 "bot loops on the same question": 62 calls, seen in 14 of 15 batches |
| Live test on Sarvam, 20 runs per agent | pass **14/20 → 19/20**; city asked more than 3 times **5 → 0**; leads lost **4 → 0** (one-sided Fisher p = 0.046) |
| Time from issue found to tested fix | **23.1 min** (offline replay) |
| Sarvam agents | `VANI-before-f146c2d8-1db4` (v1, current prompt), `VANI-after-ae6744db-fe35` (v2, fixed prompt) |

---

## 1. Tools used

**Sarvam platform (the only AI provider used)**

| Tool | Used for |
|---|---|
| Saaras `saaras:v4` speech-to-text (batch, code-mix, 2-speaker diarization) | Transcribing all 713 recordings |
| `sarvam-105b` LLM | Grading every call, extracting root-cause patterns, writing prompt fixes, playing the bot in offline replays |
| `sarvam-105b-conversations` | Playing the buyer in offline replays |
| Bulbul `bulbul:v3` text-to-speech | Before/after audio clips of a replayed call |
| Sarvam Voice Agents (Samvaad), through the **Voice Agents MCP** | Building VANI-before and VANI-after, running scored evals, live test calls |
| Sarvam Cowork | Human-in-the-loop review of the proposed prompt changes |
| Sarvam docs MCP (`searchDocs`) | Checking SDK signatures and model names instead of guessing |

**Engineering**
- Python 3.12 with `sarvamai`, `pydantic` (validating LLM output), `jinja2` (rendering the real VANI prompt template per call), `mutagen` (audio durations), `pandas` and `streamlit` (dashboard).
- **Claude Code** as our coding assistant: it wrote and debugged the pipeline with us, and drove the Voice Agents MCP to configure agents and run evals.
- Git for version control. Headless Chrome to turn the demo script into a PDF.

---

## 2. Build journey

### Step 1: Transcribe every call (Day 1)
- 713 recordings, 830.9 minutes in total. The median call is 64 s, and 74 calls are under 10 s.
- All 713 were transcribed in **22 minutes** with Sarvam batch STT, 20 files per job. The script is resumable: it skips calls already done and logs failures per file.
- **Problem:** the audio is mono 8 kHz, so diarization often mixes bot and buyer under one speaker, and the speaker number isn't stable from call to call.
- **Fix:** we assign roles by content (the bot's opening line, "IndiaMART", "Tania", "requirement", "seller"), then re-label each line and split lines where two voices were merged. About 300 of 639 conversations needed at least one line corrected.
- Silences are measured as time when *nobody* is speaking. Lines overlap a lot, so the simple gap between lines would invent dead air.

### Step 2: Rule-based signals (no LLM)
- Cheap, deterministic checks on every call:
  - how many times each field was asked, against the prompt's own limits (product 4, quantity 3, each spec 2, name/city/state 3);
  - repetition loops, dead air, a buyer asking for a human, a buyer asking "am I talking to AI?", frustration, banned questions, the bot claiming to be human, garbled audio, abrupt endings.
- These signals both feed the grader and act as a second opinion when flagging risky calls.

### Step 3: Grade every call with evidence
- `sarvam-105b` at temperature 0 judges each of the **17 parameters** in IndiaMART's call quality matrix and gives a verdict (Pass / Non-Fatal / Fatal / NA), a quote and a timestamp for each.
- **Code, not the LLM, decides the final grade.** Every quote is checked against the transcript, and an issue counts only if its quote is really in the call.
- **Problem:** with `reasoning_effort` left unset, `sarvam-105b` spent its whole 4,096-token budget thinking and returned nothing.
- **Fix:** we pass `reasoning_effort=None` explicitly and give the model a short `analysis` field to think in.
- **Problem:** our first rubric (v1) marked **76%** of calls Fatal. Human auditors mark about 12% Fatal.
- **Fix:** we rewrote the spec (v2) from IndiaMART's real audit sheet. It uses auditor-style verdicts, examples taken from auditor remarks, and a rule that a Fatal counts only at high confidence. Fatal fell to **44%** on our calls.

### Step 4: Check the grader against real auditors
- Our team's 60-call hand-label set was prepared (stratified by duration, split into tune and test halves) but never filled in. We used something better: **IndiaMART's own audit sheet** of about 4,900 auditor-graded VANI calls.
- We downloaded a stratified sample of 160 of those calls, ran the full pipeline on them blind, tuned on one half and measured on the other.
- Result: **59.0%** agreement (kappa 0.09), or **67.3%** weighted to the real mix of Pass, Non-Fatal and Fatal calls.
- **Why it isn't higher:** 21 of the 27 calls auditors marked Fatal are Fatal because of the lead record in IndiaMART's system, which isn't in the audio. No grader that only hears the call can see it.

### Step 5: Root causes by batch learning, with an importance gate
- The 567 failed calls are cut into **15 batches of 40**. The LLM extracts failure patterns per batch (map), then patterns that recur across batches are merged into named causes (reduce).
- Each cause is typed PROMPT, ASR, KNOWLEDGE or FLOW, and ranked by **priority = frequency × severity**:
  - frequency is the share of all calls the cause affects;
  - severity averages Fatal = 3 and Non-Fatal = 1 over the cause's calls.
- **Importance gate:** we didn't want every small finding to rewrite the prompt. A cause may change the prompt only if it is PROMPT-type, affects at least 10 calls and at least 1.5% of all calls, and appears in at least 3 batches.
  - Only **C3** (loops, 62 calls) and **C5** (banned questions, 12 calls) passed.
  - The biggest cause, C4 (mishearing quantities and specs, 177 calls), is ASR, so we say plainly that a prompt can't fix it.

### Step 6: Write the fix
- For each gated cause, the LLM first finds the existing rule that should have prevented the failure. It then writes one small change in the section that governs that behaviour.
- **Safety checks:** a fix is rejected if it adds numbers, prices or links, or template syntax. The run stops if any fact disappears from the prompt.
- **Problem:** an early fix replaced an existing rule instead of adding to it.
- **Fix:** edits are now additive unless the new text contains the old text.

### Step 7: Offline replay test
- A simulated buyer **replays the real buyer's lines** from each failed call, against the old prompt and the new one. The same grader scores both conversations.
- Without the real lines, simulated buyers are too cooperative to trigger the real failures.
- Bugs fixed along the way: the simulated buyer ended calls early, and cut-off replays were graded as abrupt endings.
- **Result:** ask-limit loops fell from 67% to 50% on 6 calls, with no passing call broken, and **23.1 minutes from issue to tested fix**. That sample is small, so we moved to live testing.

### Step 8: Human review in Sarvam Cowork
- We exported the evidence for Cowork: a summary, the proposed changes, and a knowledge base of all calls with names and phone numbers masked.
- **Problem:** the Cowork knowledge base rejected every .md and .txt upload.
- **Fix:** we converted the knowledge base to CSV parts.
- **Cowork's decision:** keep C3 but reword it, because our wording conflicted with the prompt's own counting rules. Drop C5, because the rule already exists and the change could make the bot close too early.
- `apply_review.py` applies the decision to produce the final prompt.

### Step 9: Live before/after agents on Sarvam (Voice Agents MCP)
- We built two agents in our own workspace from the real VANI prompt (75k characters): **VANI-before** (unchanged) and **VANI-after** (plus the reviewed fix). Both use the same voice, the Hindi, English and Telugu languages, and the demo product "10 ton soil compactor", taken from a real looping call.
- Platform lessons:
  - `@name` in a prompt is a live variable reference, so every variable must be declared.
  - This workspace allows only post-call variable extraction, not in-call updates.
  - The tool syntax is `tool:end_interaction`.
  - The VANI document holds several agent states one after another, so we used the main state only.
- **Iteration 1 failed.** With the rule in the platform's separate context field, the city-loop scenario passed only 4/10, worse than before. The prompt's own "confirm city and state" instruction won.
- **Iteration 2 worked.** We put the rule inside Collection Control and added one line to Location Collection capping city/state at 3 asks. Over 20 scored runs each:

  | | VANI-before | VANI-after v2 |
  |---|---|---|
  | Scenario passed | 14/20 | **19/20** |
  | City asked more than 3 times | 5/20 | **0/20** |
  | Lead lost ("call the seller back later") | 4/20 | **0/20** |
  | Other scenarios (vague quantity, cooperative buyer) | 3/3, 3/3 | 3/3, 3/3 |

- **Lesson:** where a rule sits in the prompt matters as much as what it says. That's why every fix gets tested before it's trusted.

### Step 10: One dashboard
- A Streamlit dashboard with five tabs:
  - **Audit:** coverage, Fatal/Non-Fatal split, agreement, and a risky-calls queue with evidence quotes and transcripts.
  - **Issues:** ranked causes with the importance gate.
  - **Fix:** prompt diffs, the watchlist, and causes a prompt can't fix.
  - **Test:** the offline replay and the timeline.
  - **Live agents:** Sarvam eval results, the iteration history, and a side-by-side transcript.
- A masked copy of its inputs (`demo_data/`) lets it run from the repo without the raw recordings.

---

## 3. Hard problems and what we learned

- **Credits ran out mid-build.** Sarvam returned HTTP 402 at 01:51 on Day 2. Every batch script was already resumable and idempotent, and LLM calls at temperature 0 are cached, so nothing was redone after the top-up.
- **Rate limit:** 40 LLM requests per minute, shared by the team. A per-process limiter (35/min) with retry and backoff kept everyone under it.
- **An LLM grader needs calibration against real auditors.** Our first version was far too strict, and only the real audit sheet showed us by how much.
- **Small samples lie.** Our first 3-run eval made iteration 1 look like a win; 10 runs showed it wasn't. Final claims use 20 runs per agent and a significance test.

## 4. Limitations (stated honestly)
- Live evals use simulated text buyers on one scenario. The real 32-ask loops in the recordings also involve misheard speech and possibly an older prompt version.
- Grades that depend on IndiaMART's system data (lead record, category) can't be judged from audio, which caps agreement with the auditors.
- The fix is a reviewable proposal tested on our own agents. Nothing was deployed to IndiaMART's production agent.

## 5. How to run
See `README.md`. In short:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# put SARVAM_API_KEY in .env
.venv/bin/python pipeline/run_all.py
.venv/bin/streamlit run pipeline/dashboard.py
```
