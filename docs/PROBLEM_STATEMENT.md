# Voice AI Hackathon 2.0 — Problem Statement 01: Quality Audit & Fix Loop

Make AI Voice Bot calls audit themselves, and get fixed automatically.

## 1. Background

AI Voice Bot handles a large volume of calls with sellers and buyers every day. Today, call quality is checked by sampling a small number of calls into a spreadsheet, where a human auditor listens and grades them. Most failures are never seen, root causes are guessed rather than measured, and fixes reach the prompt slowly and by hand.

The result is a bot that improves in occasional manual clean-ups rather than continuously. This hackathon challenges participants to close the loop: audit every call, find out why calls fail, propose a fix, and prove that the fix works.

## 2. Core Challenge

Build an end-to-end pipeline that ingests AI Voice Bot call recordings, automatically grades every call as Fatal or Non-Fatal, groups failures into root causes, ranks them by frequency and severity, suggests a prompt fix for each top issue, and tests that fix. The full journey of audit, issue, fix and test must be visible on one dashboard. The system must also flag risky calls for human review.

## 3. Problem in Detail

### 3.1 Automated Call Grading
Every call must be graded without human sampling. Participants define and justify their own criteria for Fatal and Non-Fatal, and each grade must come with evidence.
- **Grade:** Each call is labelled Fatal or Non-Fatal.
- **Reason and evidence:** Every grade carries a short reason and the transcript snippet or timestamp that supports it, so an auditor can verify it in seconds.
- **Fatal criteria:** Participants decide what is fatal. Examples to consider: wrong or misleading information given, seller or buyer requirement captured incorrectly, bot stuck in a loop, call dropped or ended abruptly by the bot, a compliance or consent breach, a clear failure to complete the call goal.
- **Coverage:** The pipeline should process 100% of the provided recordings, not a sample.

### 3.2 Root-Cause Grouping
Failed calls should not be a flat list. The system must group them into a manageable set of root causes.
- Group failures with similar causes together (for example, ASR misheard a product name, prompt lacks an instruction for an objection, bot cannot handle code-mixed Hindi and English).
- Give each root cause a clear name, a one-line description and links to example calls.
- Distinguish between prompt issues, speech or ASR issues, knowledge gaps and flow or logic issues where possible.

### 3.3 Ranking by Frequency and Severity
- **Frequency:** Share of audited calls affected by the root cause.
- **Severity:** How badly the root cause hurts the outcome (fatal versus non-fatal, lost opportunity, customer frustration).
- **Priority score:** A transparent combination of the two. Participants must explain how the score is computed.

### 3.4 Suggested Prompt Fix
For each top root cause, the system proposes a concrete change to the bot prompt.
- The fix should be specific and reviewable, for example a proposed prompt diff or an instruction to add, edit or remove, and not a generic recommendation.
- Each fix is tied back to the root cause and the calls it is expected to repair.

### 3.5 Testing the Fix
A fix is only a fix if it is tested.
- Acceptable approaches include replaying failed call transcripts or scenarios against the revised prompt with a simulated caller, or running the revised prompt on held-out calls.
- Report the failure rate before and after the fix for the affected root cause.
- Show the time taken from issue found to fix tested.

### 3.6 Risky Call Flagging
Calls that need human attention must be flagged for review:
- **Wrong requirement:** The bot captured something different from what the user wanted.
- **Frustration:** The user sounds annoyed, repeats themselves or expresses dissatisfaction.
- **Loops:** The bot repeats itself or the conversation goes in circles.
- **Low confidence:** ASR or intent confidence is low at key moments.
- **Human request:** The user asks to speak to a person.

### 3.7 One Dashboard
- **Audit:** Calls audited, Fatal versus Non-Fatal split, risky calls queue.
- **Issue:** Ranked root causes with frequency, severity and example calls.
- **Fix:** Suggested prompt change for each top issue.
- **Test:** Before and after results for each tested fix.

## 4. Resources Provided
- **Call Recordings:** A zip file of AI Voice Bot call recordings.
- **Human audit labels:** Needed to measure agreement of auto-grades with human auditors. *[To be confirmed by organisers]* If labels are not available for all calls, participants should hand-label a subset and state its size.
- **Current bot prompt:** The prompt that produced the calls, so that fixes can be written against it. *[To be confirmed by organisers]*
- **Transcripts:** Participants may generate their own transcripts using any speech-to-text service.

## 5. Submission Deliverables
- **Working Prototype:** A pipeline that runs end to end on the provided recordings, with the dashboard described above.
- **Live Demo:** A walkthrough of one root cause from audit, to issue, to suggested fix, to tested result.
- **One-Slide Presentation:** Grading logic and criteria, dashboard snapshot, and a high-level workflow covering the chosen LLM, speech-to-text, data flow and integration logic.
- **Results Summary:** Agreement with human grades, share of calls audited, and before and after failure rate for at least one tested fix.

## 6. Evaluation Criteria (indicative weights)

| Criteria | Description | Weight |
|---|---|---|
| Agreement with human auditors | How closely auto-grades match human Fatal and Non-Fatal labels | 25% |
| Share of calls audited | Proportion of calls graded automatically versus today's sampling | 10% |
| Root-cause grouping and ranking | Clarity, usefulness and defensibility of the groups and the priority ranking | 25% |
| Drop in failure rate after a fix | Measured improvement after a suggested fix is tested | 25% |
| Time from issue to tested fix | Speed of the audit → fix → test loop | 15% |

## 7. FAQs
- **Same Fatal definition as human auditors?** Not necessarily, but state your definition and show how it compares with the human labels. Where they disagree, explain why.
- **Any speech-to-text and LLM?** Yes. Name your choices on the slide.
- **No human labels?** Hand-label a representative subset yourselves, document how, and report agreement on that subset.
- **Testing without live traffic?** Replay failed transcripts or simulated scenarios against the revised prompt, or run it on held-out calls. Be clear about the method and its limits.
- **Auto-edit the production prompt?** No. A reviewable, tested fix is enough. Automatic deployment is a bonus.
