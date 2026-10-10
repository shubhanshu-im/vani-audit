# VANI call grading spec

From `docs/quality_matrix.md` and `docs/vani_buyer_prompt.md`. The buyer's seller was unavailable, so the IndiaMART help-desk bot ("Tania") confirms the product, collects quantity, specs, name and city/state, then connects a live seller or creates a buy lead (BL).

## How to grade

- Grade each **issue** FATAL or NON_FATAL. The **call** is FATAL if any issue is FATAL, otherwise NON_FATAL. A call with no issues is NON_FATAL (the rubric's "Pass").
- Every issue needs evidence: the turn timestamp and a short quote. **No evidence, no issue.**
- Give each issue a confidence of high, medium or low. Approximated parameters (≈) are never "high" unless the bot read the value back aloud.
- Judge the **bot**, not our STT (mono 8 kHz audio). Count a misrecognition only when the bot's own words show it: a read-back, a confirmation, or a reply that doesn't fit what the buyer said.
- BUYER turns sometimes contain echoed fragments of the bot's speech, and turns can overlap. An echo is not the buyer repeating themselves or agreeing.
- Short calls (<10 s) skip the grader: NON_FATAL, "no conversation".

**What we can judge:**
- **T**: can be judged from the transcript.
- **≈**: needs system data we don't have. Approximate it from what the bot said back or confirmed. If the bot never read the value back, report `not_assessed`, not an issue.
- **✕**: can't be judged. Flag only when the transcript shows the problem without any doubt.

## Rubric parameters

**1. Outcome Accuracy** (≈) — always FATAL
- Means: the call's outcome doesn't match the buyer's intent. Either a lead was made for someone who isn't buying, or a genuine buyer was closed without a lead.
- Detect: we don't have the disposition, so infer it from the bot's closing line (see the disposition table below). Compare that with the buyer's stated intent. Also count the bot ending the call mid-collection while the buyer is still engaged.
- Example: the buyer says "50 bag cement chahiye, Raipur" and the bot closes with the selling-related line "Samajh gaya. Thank you for calling Indiamart."

**2. Quantity Accuracy (Numeric)** (≈)
- FATAL if the difference matters for the category. NON_FATAL if minor (within 10–20%).
- Means: the quantity value captured is wrong.
- Detect: compare what the buyer said with the bot's read-back or confirmation.
- Example: the buyer says "200 piece" and the bot confirms "20 piece".

**3. Quantity Unit Accuracy** (≈)
- FATAL if the unit changes the lead (kg vs tonne, piece vs box). NON_FATAL for equivalent units.
- Detect: the unit in the bot's read-back differs from the buyer's unit.
- Example: the buyer says "5 ton" and the bot says "5 kilogram".

**4. Quantity Probing Error** (T)
- FATAL: the buyer never gave a quantity and the bot never asked.
- NON_FATAL: the buyer already gave the quantity and the bot asked anyway, or the nudge was improper (asked without a unit).
- Detect: look for a quantity ask in BOT turns, and check whether the buyer had already given one.
- Allowed skips, which are not an error:
  - the call ended early with no product requirement
  - the live-seller flow, after one reasonable attempt that the buyer couldn't answer
- Example: the call reaches the closing line and quantity was never mentioned.

**5. Qty Missed Opportunity** (≈)
- FATAL if the buyer clearly gave a quantity and the bot missed it. NON_FATAL if the audio was unclear (the bot said "awaaz clear nahi aayi").
- Detect: the buyer states a quantity, then the bot re-asks it, leaves it out of the read-back, or reads back a different one.
- Example: the buyer says "do sau piece chahiye" and the bot later asks "kitne piece chahiye?"

**6. Spec1 Accuracy / 7. Spec2 Accuracy** (≈)
- FATAL if the wrong value changes what a seller would quote (material, size, capacity, model). NON_FATAL if minor.
- Means: the first or second spec was captured as a different value.
- Detect: compare the buyer's answer with the bot's acknowledgement or read-back.
- Example: the buyer says "steel body" and the bot confirms "aluminium".

**8. Spec Missed Opportunity** (≈)
- FATAL: the buyer gave spec 1 or 2 and the bot didn't capture it. NON_FATAL: a 3rd or later spec was missed.
- Detect: the buyer volunteers a spec, and the bot ignores it, re-asks it, or leaves it out of the read-back. A generic "details note kar li" right after the buyer lists several specs counts as **low-confidence** NON_FATAL. It's not evidence the specs were captured.
- Example: the buyer lists "1.5, 2, 3, 5, 6.5 litre, 50% off MRP" and the bot replies only "details note kar li gayi hain" and closes.

**9. Looping Behavior** (T)
- FATAL if the repetition irritates the buyer, makes them hang up, or loses the field. NON_FATAL if it has no impact.
- Means: the bot asks for the same field more times than its ask limit (see hard rules), or repeats the same line without making progress.
- Detect: count every ask per field, including rephrasings, confirmations and clarifications. Signs of irritation: "bata to diya", "kitni baar", raised or abrupt replies, the buyer hanging up.
- Example: the bot asks for the city 4 times and the buyer hangs up.

**10. WER** (T)
- FATAL if a misheard word changes the lead (product, brand, quantity, city). NON_FATAL if it has no impact.
- Means: the bot misheard a word.
- Detect: only from the bot's own read-back or response (see "How to grade").
- Example: the buyer says "Bhilai" and the bot confirms "Bhilwara".

**11. Additional_details (BFD)** (✕)
- FATAL if grossly wrong, irrelevant or lead-damaging details were recorded. NON_FATAL for duplicate or generic details.
- Means: the extra details the bot recorded for the buyer (BFD).
- Detect: only when the bot states what it is recording, e.g. it records a complaint or job request as a product requirement. Otherwise `not_assessed`.

**12. OnCall AstBuy Error** (≈)
- FATAL: a seller was available but the bot never pitched the transfer. NON_FATAL: it didn't pitch, and the closing told the buyer the wrong next step.
- Detect: we can't see whether a seller was available. An opening like "sellers available hain jinse main abhi connect karwaungi" marks the live-seller flow. If that flow ends without the bot ever asking whether to connect the buyer, it's FATAL.
- Example: the bot promises to connect, collects the details, then says "seller aapse jald contact karenge" and ends.

**13. Product Change / No Product Accuracy** (T) — FATAL if the wrong product is approved
- Detect: the bot continues with, or confirms, a product the buyer rejected or corrected. Also when the buyer says there's no requirement and the bot still creates a lead.
- Example: the buyer says "cooker nahi, mixer grinder chahiye" and the bot asks about the cooker's body material.

**14. Category ISQ's Error** (T)
- Severity: **pending organiser confirmation.** The source's Fatal cell reads "if ISQs are not contradicting", which looks inverted. Until it's confirmed, report it as NON_FATAL, and it never decides the call grade on its own.
- Means: spec questions or options contradict each other or don't fit the product.
- Example: asking for "pressure range in bar" for a pressure cooker.

**15. MCAT / Search errors** (✕) — the rubric gives no severity
- We treat it as NON_FATAL, or FATAL only when the confirmed product itself is the wrong one (see 13).
- Detect: the spec questions clearly belong to another category. Otherwise `not_assessed`.

**16. Dead air** (T) — the rubric gives no severity, so NON_FATAL
- Means: silence over 5 s, or over 8 s if there's noise or an interruption.
- Detect: use `stats.long_gaps`. It's the bot's fault only if the gap comes **after a BUYER turn** (the bot failed to reply). Silence after a BOT turn is the buyer's.

**17. Additional Observation** (T)
- Used for anything else: pronunciation, tone, and the prompt violations below. Severity depends on the rule.

## Bot hard rules (from the prompt) and how they map

| Rule | Violation is graded as |
|---|---|
| **Ask limits:** product identification or confirmation 4, quantity 3, each spec 2, name 3, city 3, state 3, transfer consent 2. Every rephrasing or confirmation counts. | Exceeding a limit → Looping Behavior |
| **Banned open-ended questions:** "Any other details?", "Anything else?", "Any additional requirements?" (e.g. "koi aur details share karna chahenge?") | Additional Observation, NON_FATAL |
| **Languages:** Hindi, English or Telugu only. If the buyer can't use one of these, the bot says so and ends politely. | Collecting details in a language the buyer can't follow → Looping Behavior. Refusing Telugu → Additional Observation, NON_FATAL (the recordings may come from an older prompt). |
| **Never claim to be human.** The bot must say it's IndiaMART's Virtual Assistant when asked. | Additional Observation, **FATAL** (compliance) |
| **No commitments.** The bot can't promise price, stock or delivery; it defers to the seller. | A concrete price, stock or delivery promise → Additional Observation, FATAL (misleading information) |

## End-call disposition table

The bot sets the outcome, then says one fixed closing line. Use the closing line to infer the outcome for Outcome Accuracy.

| Situation | Outcome | Closing line (gist) |
|---|---|---|
| Banned or fraud product, asked again | BL Deleted / No Product Requirement | "Is product mein madad nahi kar paunga" |
| Status inquiry, no product requirement | BL Deleted / No Product Requirement | "same seller ko 10–15 min baad call karein" |
| Already shared details, refuses to share again | BL Deleted / Particular Seller | same as above |
| Refuses AI, wants a human or the original seller | BL Deleted / No Product Requirement | same as above |
| Job related, or selling related | BL Deleted / Job Related or Selling Related | "Samajh gaya. Thank you for calling Indiamart." |
| Off-topic limit reached | BL Deleted / No Product Requirement | "bhavishya mein requirement ho toh sampark kijiye" |
| Complaint continues, or wants only the seller | end call | "Complaint ke liye IndiaMART Customer Support se sampark karein" |
| Connectivity unresolved (2nd time) | end call | "I'm not able to hear you clearly, I will call you right back" |
| Normal end | BL Enriched / BL Approved | "seller jald contact karenge", or a live-seller transfer |

## Intents the bot must handle

| Intent | Expected handling | Graded as |
|---|---|---|
| **TALKING_TO_AI** | Confirm it's IndiaMART's Virtual Assistant, say the original seller was unavailable, then resume the last unanswered question. If the buyer refuses AI, acknowledge briefly and use the refuses-AI closing; don't keep persuading. | Denying it's AI → FATAL. Persuading more than once → Looping Behavior. A buyer asking for a human is also a **risky-call flag**. |
| **COMPLAINT** | Apologise once. Say IndiaMART Customer Support handles complaints, and in the same turn check for a product requirement. Don't investigate or promise a fix. If the complaint continues, use the complaint closing. | Investigating or promising a fix → Additional Observation, FATAL. Ignoring the complaint → NON_FATAL. |
| **LANGUAGE_SWITCH** | State the supported languages (Hindi, English, Telugu) and continue only if the buyer can use one. | See the language rule above. |
| **CONNECTIVITY_ISSUE** | First time: ask once "were you able to hear me?". Second time: "I'll call you right back" and end the call. | Asking more than once, or not ending on the 2nd → Looping Behavior. |
