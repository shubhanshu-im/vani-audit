# vani-audit — Current Status

_Last updated: Sat 10 Oct 2026, ~17:30 IST. Deadline: **Sat 10 Oct 2026, 23:59 IST**._

Written for a Claude chat that plans the next steps and writes prompts for Claude Code in this repo. Other references:
- Setup and commands: `README.md`
- Developer details and file formats: `CLAUDE.md`
- The brief: `docs/PROBLEM_STATEMENT.md`

---

## 1. Goal (one paragraph)

Hackathon PS-01 "Quality Audit & Fix Loop" for IndiaMART's buyer-side voice bot **VANI**:
1. Transcribe every call.
2. Grade each call Fatal / Non-Fatal with evidence.
3. Flag risky calls.
4. Find root causes in batches, ranked by frequency × severity. Only important, recurring causes may update the VANI prompt.
5. Write the prompt fix.
6. Prove it works by replaying calls with the old and new prompt, and live on Sarvam voice agents.
7. Show it all on one dashboard.

Scoring: human agreement 25%, root causes + ranking 25%, failure drop after fix 25%, issue→tested-fix speed 15%, calls audited 10%. Sarvam only.

**Team:**
- **Person 1**: transcription, signals, grading, agreement.
- **Person 2**: root causes → fix → test → dashboard. Their code is merged into `pipeline/`.
- **Person 3**: labels.

---

## 1c. Update: Sat 10 Oct, ~17:30 IST (Cowork review done; live Sarvam agents show the fix working)

- **Cowork review** (`outputs/cowork_review.json`):
  - C3 (repeated questions): **reworded.** The original wording conflicted with the prompt's own counting rules.
  - C5 (banned question): **dropped.** It already exists, and the change could make the bot close too early.
  - Final prompt: `outputs/prompts/vani_prompt_final.md`, built by `pipeline/apply_review.py`. It is the current prompt plus two added lines, nothing removed:
    1. the C3 rule in Collection Control;
    2. iteration 2 adds a 3-ask cap line to Location Collection Principles.
- **Live Sarvam voice agents** (via the Voice Agents MCP; IDs in `outputs/agents/agents.json`):
  - **VANI-before** `VANI-before-f146c2d8-1db4` v1: the real VANI prompt, main redirect-flow state, 75k characters, rendered by `pipeline/agent_prompts.py`.
    - Demo product "10 ton soil compactor", no live seller.
    - Voice "Aparna – Support Agent"; Hindi, English and Telugu.
    - 19 post-call variables, such as call_outcome, quantity, quantity_asked_count and call_step=LOOP_DETECTED.
  - **VANI-after** `VANI-after-ae6744db-fe35` **v2**: the same prompt plus the two lines (`outputs/agents/vani_after.txt`). v1 had the rule only in the platform's context field.
- **Platform facts learned:**
  - `@name` in a prompt is a live variable/tool reference, so VANI's prompt is native Sarvam syntax.
  - This workspace has one state per agent and doesn't allow in-call variable updates; variables are filled after the call instead.
  - The .docx holds several agent states one after another: main, Enrichment, Redial and Redial_AST.
  - No phone connection exists yet, so tests run as text chat and Sarvam evals. The Samvaad test panel supports web calls.
- **Sarvam evals** (simulated buyers, platform-scored; scenarios in `outputs/agents/eval_suite.json`, results in `outputs/agents/eval_results.json`, raw runs in `outputs/agents/runs/`). Target scenario: "Cannot give a usable city" (buyer says "paas mein hi hai, seller bhej do").

  | | VANI-before v1 | VANI-after v1 (rule in context field) | **VANI-after v2 (rule in prompt)** |
  |---|---|---|---|
  | City scenario passed | 14/20 (70%) | 4/10 (40%) | **19/20 (95%)** |
  | Asked city/state more than 3 times | 5/20 | 5/10 | **0/20** |
  | Lead dropped ("call the same seller back") | 4/20 | 3/10 | **0/20** |
  | Vague quantity / cooperative buyer (regression) | 3/3, 3/3 | 3/3, 3/3 | 3/3, 3/3 |

  The before vs after-v2 difference is significant: one-sided Fisher p = 0.046.
- **Lesson:** where the rule sits matters. In a side field it lost to the Location section's own "confirm city and state" instruction. Placed in the section that governs the behaviour, it worked.
- **Honest caveats:**
  - These are simulated text buyers on one scenario.
  - On clean text, the current prompt already respects most ask limits. The real 32-ask loops in the recordings also involve misheard speech and possibly an older prompt.
  - The quantity-loop variant was not reproduced: both agents pass it.
- **Dashboard:** the "Live agents" tab shows:
  - both agents and the two-line difference
  - the eval scores and the iteration table
  - a side-by-side transcript of the same stubborn buyer

## 1b. Update: Sat 10 Oct, 12:50 IST (credits topped up; latest results)

- **Agreement with IndiaMART auditors** (80 held-out audited calls, grading spec v2, decision rule "a FATAL counts only at high confidence", rule chosen on the tune split):
  - **59.0%** Fatal-vs-not, kappa 0.09; **67.3%** weighted to real traffic.
  - Why it's low: 21 of 27 auditor-Fatal calls are Fatal because of the **lead record (BL) in IndiaMART's system**, which isn't in the audio.
  - Next step beyond the hackathon: feed the saved BL fields into the grader.
- **All 713 calls regraded with v2:**

  | | Calls |
  |---|---|
  | Fatal | 282 (44% of 639 calls with a conversation; v1 had 76%) |
  | Non-Fatal | 285 |
  | Pass | 72 |
  | No conversation | 74 |

- **Batch learning:** 567 failed calls, 15 batches of 40, 90 patterns, 20 causes. **Importance gate passed by only 2:**
  - C3 "Bot loops on the same question" (PROMPT, 112 calls, 14/15 batches)
  - C5 "Bot asks banned open-ended questions" (PROMPT, 33 calls, 7 batches)
- **Biggest causes the prompt can't fix:**
  - C4 mishears quantity/specs (ASR, 177 calls)
  - C1 promises WhatsApp instead of a live seller (FLOW, 150 calls)
- **Prompt fixes:** 2 additive changes in `outputs/prompts/vani_prompt_v2.md`. Edits now never delete an existing rule; an edit once tried to replace the LOOP_DETECTED rule, and that's now blocked.
- **Cowork export:** `outputs/cowork/` is regenerated (21 files, 15 batches).
- **Voice Agents MCP:** added to Claude Code (`sarvam-voice-agents`) but needs the user's Sarvam sign-in (`/mcp` → Authenticate).
- **Not rerun, to save credits:** the simulated fix test and voice clips. The live Sarvam agents replace them for the demo.

## 2. 🔴 Earlier blocker (resolved 10 Oct ~12:15): Sarvam credits exhausted

Since **10 Oct 01:51 IST**, every Sarvam API (LLM, STT, TTS) returns `402 No credits available`. Nothing that calls Sarvam can run until the account is topped up. All code is written and tested up to that point.

**Usage so far** (from our logs and LLM cache):

| What | Volume | Purpose |
|---|---|---|
| Speech-to-text (saaras:v4, batch) | ~1,170 min audio = 873 calls | 713 hackathon calls (831 min) + 160 human-audited calls for calibration (339 min) |
| LLM grading (sarvam-105b) | ~1,100 calls, ~5.6M in / ~1.8M out tokens | Grading all 713 calls once, plus retries and re-grades after fixes to the transcripts and schema |
| LLM fix-test simulation (sarvam-105b bot + sarvam-105b-conversations buyer) | ~250 bot/persona calls, ~4.8M in tokens; ~400 short buyer turns | Replaying 10 real calls with the old and new prompt, 3–4 runs while debugging the simulator. **Most expensive per call**: every bot turn sends the full ~30k-token VANI prompt. |
| LLM root causes / prompt fixes / checks | ~110 calls, ~0.75M in tokens | Clustering failures, writing 3 prompt fixes (each sends the full prompt), "is the problem still visible" checks |
| Text-to-speech (bulbul:v3) | ~3,200 characters | Before/after demo audio for one call, plus 2 voice tests |
| **Total LLM** | **~12M input + ~1.9M output tokens** | |

---

## 3. Where each stage stands

| # | Stage | State | Notes |
|---|---|---|---|
| 1 | Transcribe | ✅ 713/713 + 160/160 audit calls | |
| 2 | Normalise (BOT/BUYER roles, silences) | ✅ | Roles are fixed line by line (Sarvam diarization mixes speakers) |
| 3 | Rule-based signals | ✅ 713 + 160 | Loops / ask counts, dead air, banned question, frustration, human request, low-ASR, abrupt end |
| 4 | Grade | ⚠️ 713 graded with **spec v1, which is too strict** (76% FATAL vs auditors' ~12%). **Spec v2 is written but not yet run** (credits). | v2 = auditor-style verdicts per parameter (PASS / NON_FATAL / FATAL / NA), rules and examples taken from auditor remarks, narrowed "Outcome Accuracy" |
| 5 | Agreement with humans | ⏳ ready, not run | Uses IndiaMART's **real audit sheet**: 160 audited calls downloaded and transcribed (80 tune / 80 test, stratified Fatal / Non-Fatal / Pass). Reports binary, 3-class and population-weighted agreement, plus per-parameter agreement. |
| 6 | Root causes (batch learning) | ✅ code · ⏳ rerun | Failed calls in batches of 40 → patterns per batch → merged causes, with call and batch counts. The last run (old method, v1 grades) is in outputs/. |
| 7 | Rank + importance gate | ✅ code · ⏳ rerun | Priority = freq% × severity (Fatal 3 / Non-Fatal 1). **Gate:** a cause may update the prompt only if it is PROMPT-type, has ≥10 calls and ≥1.5% of calls, and appeared in ≥3 batches; the rest go on a watchlist. |
| 8 | Prompt fixes | ✅ ran once (v1 data) | One small behaviour-only edit per gated cause; diffs in `outputs/prompt_diff_*.txt`; updated prompt in `outputs/prompts/vani_prompt_v2.md` |
| 9 | Fix test (simulated replay) | ✅ ran once · ⚠️ weak result | The bot runs the real prompt (Jinja template rendered per call); the simulated buyer reuses the real buyer's lines. Cause "asks the same question too many times": loops 67%→50%, FATAL 83%→83% (n=6), 0 regressions, 23 min issue→tested. Needs a rerun after v2 grading. |
| 10 | Cowork review + live demo on Sarvam Voice Agents | ⏳ export ready (`export_cowork.py`), MCP planned | Create 2 agents (current prompt vs fixed prompt) via the App Authoring API (`https://apps.sarvam.ai/api/app-authoring`, `X-API-Key`), commit versions, talk to both live, add a "Live demo" tab. **Needs credits + our own org_id and workspace_id. Never use IndiaMART's production workspace** (`indiamart.com` / `indiamart-com-defa-22f36d`). |
| 11 | Voice clips, summary, dashboard | ✅ | `outputs/audio/before|after.wav`, `outputs/results_summary.md`. Run the dashboard with `.venv/bin/streamlit run pipeline/dashboard.py`; it shows auditor agreement, the gate and the watchlist. |
| – | One-slide deck | ⬜ | Not started |
| – | Git | ⚠️ | Only the initial commit; everything since is uncommitted |

---

## 4. What the audit sheet taught us (use in the slide)

- "Audit Source of truth.csv" (now at `data/audit/audit_source_of_truth.csv`, git-ignored): about 4,900 VANI calls hand-graded by IndiaMART auditors with our rubric.
- None of them are our 713 calls. The SQUADSTACK recordings (2,866) download freely; the Sarvam-hosted ones need a login.
- **Human base rates** (inbound SQUADSTACK, n=1,877):
  - Overall: 69% Pass / 19% Non-Fatal / 12% Fatal.
  - Call Quality Fatal: 3.5%.
  - Outcome Accuracy Fatal: 2.8%.
  - Most Fatals are wrong values in the lead (quantity or spec), the wrong product, or a lead created without a real requirement.
- Our v1 grader marked 76% Fatal, with Outcome Accuracy on 73% of calls. That's why spec v2 exists.

---

## 5. Next steps, in order (once credits are back)

```bash
# 1. Baseline vs calibrated grader on the 160 audited calls (~1.3M tokens each)
VANI_DATASET=audit .venv/bin/python pipeline/grade.py --spec prompts/grading_spec_v1.md
mv outputs/audit/grades outputs/audit/grades_v1
VANI_DATASET=audit .venv/bin/python pipeline/grade.py
VANI_DATASET=audit .venv/bin/python pipeline/agreement.py
# 2. Tune prompts/grading_spec.md on the "tune" split only (read the disagreements in outputs/audit/agreement.json),
#    regrade with --force, and report the "test" split as the headline number.
# 3. Regrade the 713 calls with the calibrated spec (~6.5M tokens), then the rest of the loop
.venv/bin/python pipeline/grade.py --force
.venv/bin/python pipeline/run_all.py --from 5      # agreement, batch learning, gate, fixes, fix test, voice, summary
.venv/bin/python pipeline/export_cowork.py          # -> outputs/cowork/ (files to upload to Sarvam Cowork)
# 4. Cowork: knowledge base = calls.csv + batches/*.md + both prompts; chat = 00_INSTRUCTIONS + 01_summary + 02_proposed_changes
#    -> Cowork returns the approved change table and the final prompt
# 5. Voice Agents MCP (https://mcp.sarvam.ai/voice-agents, Sarvam SSO): clone the agent into "VANI-before" / "VANI-after",
#    set the prompts, commit; live demo in the Samvaad test panel. Use Claude Code if Cowork can't add the MCP. Never touch the production agent.
# 5. Slide + demo walkthrough (one root cause: audit -> batch learning -> gated fix -> tested -> live agent)
```

To save credits:
- Keep fix tests to about 10 calls (`--max-target 6 --n-reg 4`).
- Don't run two LLM-heavy scripts at once (rate limit is 40 req/min per account).
- Temperature-0 LLM calls are cached in `outputs/.cache/`, so re-runs of unchanged steps are free.

---

## 6. Key decisions (don't re-litigate)

- **Sarvam only:**
  - STT: saaras:v4.
  - Grading, clustering, fixes and the simulated bot: sarvam-105b.
  - Simulated buyer: sarvam-105b-conversations.
  - TTS: bulbul:v3 ("tanya" for the bot, "rahul" for the buyer).
  - Reasoning is off on every LLM call: `reasoning_effort=None`, otherwise answers come back empty.
- **Code decides the grade:** any FATAL backed by a quote verified in the transcript → FATAL. `quality` is PASS / NON_FATAL / FATAL, as the auditors use it.
- **Cause types:** VANI is prompt-driven, so its conversation behaviour is PROMPT; FLOW means telephony, tools or data; ASR means mishearing; KNOWLEDGE means missing facts.
- **Fixes:** behaviour-only; never new numbers, prices or links; only for causes that pass the importance gate.
- **Code layout:** one codebase in `pipeline/` (steps 1–11 in `run_all.py`). `VANI_DATASET=audit` switches to the audit calls.
- **Privacy:** per-call outputs and anything quoting buyers are git-ignored. `docs/` holds IndiaMART's internal prompt, so any GitHub repo must be **private**.
- **Open questions for the organisers:**
  - Which prompt version produced the calls? The bot refuses Telugu and asks the banned question, both against the current prompt.
  - Is the "Category ISQ's Error" Fatal cell written backwards?
