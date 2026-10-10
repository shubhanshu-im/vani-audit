# PROMPT: Build "Person 2" part of the VANI Quality Audit & Fix Loop

You are my coding assistant. Build everything described below. Work step by step, run each script on sample data to check it, and fix errors yourself. Keep the code simple and readable, with short comments. Do not add extra features.

## 1. Background
I am in IndiaMART's internal Voice AI Hackathon 2.0 (9-10 Oct 2026). Submission deadline: Sat 10 Oct, 11:59 PM.
VANI is IndiaMART's AI voice bot. It calls sellers and buyers in Hindi, English and Hinglish. Humans check only a small sample of calls, so most failures go unseen and prompt fixes are slow.
Our team is building a system that: transcribes every call, grades each call FATAL or NON_FATAL with evidence, measures agreement with human auditors, groups failures into root causes and ranks them, writes prompt fixes, tests fixes by replaying calls with old vs new prompt, flags risky calls, and shows all this on a dashboard.

I am **Person 2**. Person 1 builds transcription, grading and agreement. Person 3 writes rubric.md and labels. **You build only my part** (below). Do not rebuild Person 1's work, but read their output files.

## 2. Rules and tools
- Only Sarvam AI services: LLM `sarvam-105b` (or `sarvam-30b` for speed) for all reasoning, Bulbul for text-to-speech. No OpenAI/Anthropic/other APIs.
- Python 3.10+, Streamlit dashboard, all results saved as JSON files in `outputs/`.
- API key from env var `SARVAM_API_KEY`. Never hard-code it.
- Sarvam chat endpoint: `https://api.sarvam.ai/v1/chat/completions`, header `api-subscription-key`. TTS endpoint: `https://api.sarvam.ai/text-to-speech`. Check the current Sarvam docs for exact model names and Bulbul speaker names, and make them configurable by env vars.
- Strip any `<think>...</think>` text from LLM replies. Retry failed calls 3 times. When asking for JSON, parse safely.
- Handle Hindi/Hinglish text correctly (UTF-8, `ensure_ascii=False`).

## 3. Files I receive from Person 1 (do not change them)
Transcript `outputs/transcripts/{call_id}.json`:
`call_id, duration, segments: [{speaker: BOT|CALLER, start, end, text, text_en}]`

Grade `outputs/grades/{call_id}.json`:
`grade (FATAL|NON_FATAL), fatal_criteria_hit [list], reason, evidence [{quote, timestamp}], failure_description, risk_flags [list], confidence`

Also available: `rubric.md` (grading rules by Person 3), `outputs/timeline.json` (stage timings), optional `outputs/agreement.json` like `{"agreement_pct": 82}`, recordings in `data/recordings/`.
Person 1's grader may be importable as `from grader import grade_transcript` taking a transcript dict and returning a grade dict. Try it; if it does not exist, fall back to my own LLM grader that follows rubric.md.
Start working with the first 20 graded calls, and make everything work for all calls later.

## 4. What to build (in this order)

### Step A: Bot prompt file
Create `prompts/vani_prompt_current.md`. If the real VANI prompt is not given, write a short RECONSTRUCTED prompt by reading what the BOT says across about 15 transcripts. The first line must say `# RECONSTRUCTED PROMPT`. Show "reconstructed" on the dashboard.

### Step B: Root causes (`01_root_causes.py`)
1. Load all grades where `grade == FATAL` or `failure_description` is not empty.
2. Send up to 80 failure descriptions to the LLM. Ask for 5-10 root causes, each with: `id` (C1, C2...), `name` (simple words a non-technical person understands, e.g. "Bot ignores 'already have a supplier'"), one-line `description`, and `type` = PROMPT (fixable by instructions) | ASR (speech misheard) | KNOWLEDGE (bot lacks info) | FLOW (call logic broken).
3. Assign every failed call to one cause id or "Other" (batches of 20).
4. Save `outputs/root_causes.json` = `{causes: [...], assignments: {call_id: cause_id}}`.
5. Print each cause with its call count so I can check by eye (3 calls per cause). I may edit the JSON by hand.
6. Write the stage time `root_causes_found` into `outputs/timeline.json` (ISO time + epoch).

### Step C: Ranking (`02_rank.py`)
For each cause: `frequency_pct` = calls in cause / ALL audited calls x 100; `severity` = average of (FATAL=3, NON_FATAL=1) over its calls; `priority = frequency_pct x severity`. Sort by priority descending, attach 3 example call ids. Save `outputs/ranking.json` including the written formula: "Priority = Frequency x Severity".

### Step D: Prompt fixes (`03_prompt_fix.py`)
1. Take the top 2-3 causes of type PROMPT. For ASR/KNOWLEDGE/FLOW causes, do not write a prompt fix; list them under `not_fixable_by_prompt`, because honesty matters here.
2. For each, ask the LLM for ONE small, specific change: `action` (add|edit|remove), `where` (which section), `new_text`, `why`, linked to example calls.
3. HARD RULE: fixes change how the bot behaves only. Never change facts, prices or compliance statements. Add this rule to the LLM instructions and check the output for it.
4. Apply changes to make `prompts/vani_prompt_v2.md` (everything else identical). Save a unified diff to `outputs/prompt_diff.txt` and the fixes to `outputs/fixes.json` with a `created_at` time per fix. Write `fix_created` into timeline.json.

### Step E: Test the fix (`04_test_fix.py`), most important (25% + 15% of score)
Take ONE cause (argument, default = top PROMPT cause). Get one cause fully working before any second.
1. Test set = all failed calls of that cause + 20 calls that passed (NON_FATAL, not in any cause) as regression check.
2. For each call, ask the LLM to write a "caller persona" from the real transcript: what they wanted, what they said, language, mood, how they reacted.
3. Simulate two conversations per call, up to 8 bot+caller turn pairs:
   - BEFORE: bot LLM uses the old prompt as system prompt.
   - AFTER: bot LLM uses the fixed prompt.
   - Bot speaks first. The caller LLM plays the persona (short replies, same language) and ends with `[END]` when the call is naturally over. Remember to flip roles correctly for each LLM.
4. Grade every simulated conversation with Person 1's grader (or the fallback grader).
5. Compute and save to `outputs/test_results.json`:
   - target failure rate BEFORE and AFTER (% FATAL), with counts
   - regression: list of passing calls that now fail (should be zero or near zero)
   - `minutes_issue_to_tested_fix` = time from `root_causes_found` to `fix_tested` in timeline.json
   - caveat text: "Callers are simulated by an LLM; prompt may be reconstructed"
   - all simulated conversations with grades and reasons
6. Write `fix_tested` into timeline.json.

### Step F: Voice clips (`05_voice.py`)
Pick one call that was FATAL before and not FATAL after. Use Bulbul to voice every turn (target language `hi-IN`), using a different voice for BOT and CALLER, join the audio with the standard `wave` module, and save `outputs/audio/before.wav` and `outputs/audio/after.wav`.

### Step G: Dashboard (`dashboard.py`, Streamlit, 4 tabs)
- **Audit**: number of calls audited, Fatal vs Non-Fatal bar chart, agreement score (from agreement.json, show "n/a" if missing), a risky-calls list filterable by risk flag, each showing reason, evidence quotes with timestamps, and an audio player if the recording exists.
- **Issues**: ranked table of root causes (frequency, severity, priority, type), the formula shown in text, and 3 example calls per cause.
- **Fix**: each fix (action, where, why, new text), the old-vs-new prompt diff with red/green colouring, a note that the prompt is reconstructed if so, and a warning listing causes that a prompt cannot fix.
- **Test**: big metrics for failure rate before -> after, regression result, time-to-fix; before.wav and after.wav players; expandable table of all simulated calls; the caveat text.
Every tab must show a friendly message (not crash) if its input file does not exist yet.

### Step H: One-click run (`run_person2.py`)
Runs steps B to F in order, stops with a clear message if one fails, and prints a short summary at the end (causes found, top cause, failure rate before -> after, regressions, minutes to fix).

### Step I: README section
Write `README_person2.md`: how to set up and run, the method in simple words, files produced, and limitations (testing uses simulated callers; prompt reconstructed if not provided; ASR/knowledge issues are not fixable by prompt; small sample sizes).

## 5. Quality checks before you say "done"
- Every script runs from the project root with only `SARVAM_API_KEY` set, and re-running it is safe.
- JSON files follow the formats above exactly (other team members read them).
- No crash if some calls have missing fields or empty `failure_description`.
- Test on at least 5 real graded calls before running on all.
- Show me the final folder tree, the commands to run, and any assumption you made (for example API model names or the grader function name).

## 6. Style
Simple code, no heavy frameworks, clear names. Explain your changes to me in plain, simple language.
