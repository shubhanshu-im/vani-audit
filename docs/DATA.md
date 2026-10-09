# Data

Source: the hackathon "Aids to solve Problems" bundle. `data/` is git-ignored because it holds call audio and buyer details. We're solving Problem Statement 01 (see `PROBLEM_STATEMENT.md`), which needs three things: the recordings, the bot prompt, and the auditors' rubric.

## In the repo

| Path | What it is |
|---|---|
| `data/recordings/*.mp3` | 713 buyer-side VANI call recordings (830.9 min total, median 64 s). Filename: `<index>_<call_id>_<recording_code>.mp3`. 74 are under 10 s (near-empty or dropped calls). |
| `data/transcripts/raw/` | Raw Sarvam STT output per call (3 done so far: the STT test calls). `clean/` holds the normalised versions. |
| `data/labels/` | Empty. Our hand-labelled subset goes here, since no human labels were provided. |
| `docs/quality_matrix.png` / `.md` | The human auditors' Call Quality Matrix (parameters, fatal and non-fatal rules). |
| `docs/vani_buyer_prompt.docx` / `.md` | The bot's system prompt, the spec that fixes are written against. The `.docx` is the original. |

## Left out

- `Call_Logs_Hackathon.csv`: 1,000 VANI call logs, but none match the recordings by any ID, so they can't be used for grading.
- `Global Context - Seller Dataset.zip`, `Global_Context_DATA_DICTIONARY.md.docx`, `Global_Context_Problem_Statement_Buyer Side.csv`: Global Context (memory) track.
- `Persona Files-*.zip`: voice persona track.
- `BesTime to Call/`: best-time-to-call track.
- `Seller Enrichment-*/`: seller onboarding track.

The original bundle is still in `~/Downloads/Aids to solve Problems-20261009T085102Z-1-001.zip`.
