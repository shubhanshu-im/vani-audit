# Results summary (auto-generated 2026-10-10T12:49:53+05:30)

- Calls audited: **713 of 713 recordings (100%)**
- Fatal: **282** (44% of 639 calls with a conversation) | Non-Fatal: 357 | No conversation (<10 s): 74
- Risky calls flagged for human review: 547
- Agreement with human auditors: n/a (no human labels yet)

## Top root causes
Priority = Frequency x Severity. Frequency = % of ALL audited calls in the cause. Severity = average over the cause's calls of Fatal = 3, Non-Fatal = 1.

1. Bot mishears or misses buyer's quantity and specs [ASR]: 177 calls (24.8%), severity 2.24, priority **55.7**
2. Bot promises WhatsApp instead of live seller or lead [FLOW]: 111 calls (15.6%), severity 2.05, priority **31.8**
3. Other [OTHER]: 135 calls (18.9%), severity 1.55, priority **29.3**
4. Bot loops on the same question instead of moving forward [PROMPT]: 62 calls (8.7%), severity 1.94, priority **16.8**
5. Bot closes call or creates lead without collecting details [FLOW]: 32 calls (4.5%), severity 2.31, priority **10.4**

## Prompt fixes
- Bot loops on the same question instead of moving forward: EDIT (edited): This prevents the bot from counting rephrased versions of the same question as new attempts and forces it to stop probing after the limit is reached.
- Bot asks banned or irrelevant open-ended questions: EDIT (added at the end): This makes the banned question rule actionable at the moment the bot decides its next reply, forcing it to close the field and move on instead of asking the open-ended prompt.
- Not fixable by prompt: Bot mishears or misses buyer's quantity and specs [ASR]; Bot promises WhatsApp instead of live seller or lead [FLOW]; Bot closes call or creates lead without collecting details [FLOW]; Bot misidentifies buyer's product or intent [KNOWLEDGE]; Bot closes without collecting quantity, specs, or buyer details [FLOW]; Bot asks for quantity after buyer already stated it [FLOW]; Bot closes the call or creates a lead without collecting required details [FLOW]; Bot loops on product confirmation or specs [FLOW]; Bot fails to collect name, city, or state before closing [FLOW]; Bot closes prematurely without collecting required lead details [FLOW]; Bot closes call as no requirement despite buyer stating a product need [FLOW]; Bot fails to capture critical specs and closes with vague WhatsApp offer [FLOW]; Bot fails to collect required lead details like name and location [FLOW]

## Fix test
- Cause: Bot asks the same question too many times [PROMPT]
- Failure rate (Fatal): **83.3% -> 83.3%** (5/6 -> 5/6 calls)
- Cause still visible in the conversation: 100.0% -> 100.0%
- Bot exceeded an ask limit (rule-based loop check): 66.7% -> 50.0%
- Held-out calls (not used to write the fix, n=3): 100.0% -> 100.0%
- Regression: 0 of 4 passing calls now fail
- Time from root cause found to tested fix: **23.1 min**
- Caveat: Buyers are simulated by an LLM from the real call, not real people; the bot is the real prompt run on sarvam-105b in text, without ASR/TTS or real tool calls.
