"""Pick the hand-label subset and export it for human review.

Selects --n calls (default 60) from the clean transcripts, excluding short calls (<10 s),
stratified by duration: non-short calls are split into --bins equal-count duration bins
(quantiles) and the same number is drawn from each, so the sample mirrors the real
duration mix. The chosen calls are sorted by duration and paired off; one call of each
pair goes to "tune" and the other to "test", so both halves have matching durations.

Writes:
    data/labels/human_labels.csv      one row per call, label columns left blank
    data/labels/review/{call_id}.txt  readable transcript for the labeller
    data/labels/label_set.json        seed, bins and per-call duration (for reproducibility)
    data/labels/LABELLING_GUIDE.md    instructions + allowed rubric_params values

Never overwrites a CSV that already contains labels.

Usage:
    .venv/bin/python pipeline/make_label_set.py                  # after all calls are normalised
    .venv/bin/python pipeline/make_label_set.py --allow-partial --out-dir <tmp>   # dry run
"""
import argparse
import csv
import random
import shutil
from pathlib import Path

from common import CLEAN_DIR, RECORDINGS, ROOT, now_iso, read_json, rubric_params, setup_logging, write_json

LABELS_DIR = ROOT / "data" / "labels"
COLUMNS = ["call_id", "split", "grade", "rubric_params", "fatal_reason", "notes", "labelled_by"]
LABEL_COLUMNS = ["grade", "rubric_params", "fatal_reason", "notes", "labelled_by"]

log = setup_logging()


def load_calls():
    calls = []
    for p in sorted(CLEAN_DIR.glob("*.json")):
        c = read_json(p)
        calls.append(c)
    return calls


def select(calls, n, bins, seed):
    rng = random.Random(seed)
    pool = sorted((c for c in calls if not c["short_call"]), key=lambda c: (c["duration_s"], c["call_id"]))
    if len(pool) < n:
        raise SystemExit(f"only {len(pool)} non-short calls available, need {n}")
    if n % bins or n % 2:
        raise SystemExit("--n must be even and divisible by --bins")
    size = len(pool) / bins
    strata = [pool[round(i * size): round((i + 1) * size)] for i in range(bins)]
    chosen, bin_info = [], []
    for i, stratum in enumerate(strata):
        picks = rng.sample(stratum, n // bins)
        for c in picks:
            c["_bin"] = i
        chosen += picks
        bin_info.append({"bin": i, "min_s": stratum[0]["duration_s"], "max_s": stratum[-1]["duration_s"],
                         "calls_in_bin": len(stratum), "picked": len(picks)})
    chosen.sort(key=lambda c: (c["duration_s"], c["call_id"]))
    for a, b in zip(chosen[::2], chosen[1::2]):
        tune, test = (a, b) if rng.random() < 0.5 else (b, a)
        tune["_split"], test["_split"] = "tune", "test"
    return chosen, bin_info


def mmss(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def review_text(c, split):
    audio = RECORDINGS / f"{c['call_id']}.mp3"
    lines = [
        f"CALL      {c['call_id']}",
        f"AUDIO     {audio.name}",
        f"          {audio}",
        f"DURATION  {mmss(c['duration_s'])}  ({c['duration_s']:.0f} s)",
        f"LANGUAGE  {c.get('language_code')}",
        f"SPLIT     {split}",
        "",
        "Transcript is machine-generated (Sarvam STT). Listen to the audio when in doubt.",
        "Times are [minutes:seconds] from the start of the recording.",
        "BUYER lines can contain fragments of the bot's own words (speaker-separation errors).",
        "",
        "-" * 78,
    ]
    def silence(g):
        return f"          ... {g['duration_s']:.1f} s of silence ({mmss(g['start'])}-{mmss(g['end'])}) ..."

    pending = sorted(c["stats"].get("long_gaps", []), key=lambda g: g["start"])
    for t in c["turns"]:
        # a silence goes right before the first line that starts once it's over
        while pending and pending[0]["end"] <= t["start"] + 0.01:
            lines.append(silence(pending.pop(0)))
        lines.append(f"[{mmss(t['start'])}] {t['role']:<5}  {t['text']}")
    lines += [silence(g) for g in pending]
    lines += ["-" * 78, f"[{mmss(c['duration_s'])}] END OF RECORDING", ""]
    return "\n".join(lines)


def guide_text(params, n_tune, n_test):
    plist = "\n".join(f"- `{p}`" for p in params)
    return f"""# Labelling guide

You're labelling {n_tune + n_test} VANI bot calls ({n_tune} "tune", {n_test} "test") so we can measure how
well the automatic grader agrees with a human. Label both splits the same way, and
**don't look at the grader's output for a call before you label it**.

## For each call

1. Open `review/<call_id>.txt` and play the audio file named at the top (in `data/recordings/`).
   The text is machine-transcribed, so trust the audio when the two disagree.
2. Judge the call against `docs/quality_matrix.md` (rubric) and what the bot is supposed to do.
3. Fill in that call's row in `human_labels.csv`. Leave `call_id` and `split` as they are.

## Columns

| Column | What to write |
|---|---|
| `grade` | `FATAL` or `NON_FATAL` (capitals, underscore). A call with no problems at all is `NON_FATAL`. |
| `rubric_params` | Every rubric parameter that went wrong, separated by `;`, copied exactly from the list below. Leave empty if nothing went wrong. |
| `fatal_reason` | For `FATAL` calls: one short sentence on the main reason, ideally with the time, e.g. `00:42 bot captured "2 litre" but buyer said 5 sizes`. Empty for `NON_FATAL`. |
| `notes` | Anything else worth knowing: unclear audio, transcript errors, things the rubric doesn't cover. |
| `labelled_by` | Your name or initials. |

A call is `FATAL` if **any** problem in it is fatal under the rubric's "Fatal" column
(e.g. wrong product or quantity captured, seller available but not pitched, a loop that
annoys the buyer into hanging up). Otherwise it's `NON_FATAL`, even if minor issues exist.

## Allowed `rubric_params` values

{plist}

Example: `Quantity Probing Error;Looping Behavior`

## Tips

- Bot rules worth knowing (from the bot's prompt): it may ask for the product up to 4 times,
  quantity 3, each specification 2, name / city / state 3 each. It must never ask open-ended
  questions like "any other details?". It may speak Hindi, English or Telugu only, and must
  never claim to be human.
- Silences over 5 seconds are marked in the transcript as `... N s of silence ...`.
- Save the CSV as CSV (UTF-8) if you edit it in Excel or Google Sheets.
"""


def existing_labels(path):
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        return sum(any((row.get(k) or "").strip() for k in LABEL_COLUMNS) for row in csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=60, help="calls to select (even, divisible by --bins)")
    ap.add_argument("--bins", type=int, default=4, help="duration strata (equal-count quantile bins)")
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--out-dir", type=Path, default=LABELS_DIR)
    ap.add_argument("--allow-partial", action="store_true",
                    help="run even if not every recording has a clean transcript (for testing only)")
    ap.add_argument("--force", action="store_true",
                    help="replace an existing, still-empty label set")
    args = ap.parse_args()

    out = args.out_dir.resolve()
    csv_path = out / "human_labels.csv"
    filled = existing_labels(csv_path)
    if filled:
        raise SystemExit(f"{csv_path} already has {filled} labelled row(s); refusing to overwrite. "
                         "Move it aside first if you really want a new set.")
    if csv_path.exists() and not args.force:
        raise SystemExit(f"{csv_path} already exists (no labels yet). Re-run with --force to regenerate.")

    calls = load_calls()
    n_rec = len(list(RECORDINGS.glob("*.mp3")))
    if len(calls) < n_rec and not args.allow_partial:
        raise SystemExit(f"only {len(calls)}/{n_rec} recordings have clean transcripts. Finish "
                         "transcribe_all.py + normalise.py first, so the sample represents every call.")

    params = rubric_params()
    chosen, bin_info = select(calls, args.n, args.bins, args.seed)

    review = out / "review"
    if review.exists():
        shutil.rmtree(review)
    review.mkdir(parents=True)
    for c in chosen:
        (review / f"{c['call_id']}.txt").write_text(review_text(c, c["_split"]), encoding="utf-8")

    ordered = sorted(chosen, key=lambda c: (c["_split"] != "tune", c["call_id"]))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for c in ordered:
            w.writerow({"call_id": c["call_id"], "split": c["_split"]})

    n_tune = sum(c["_split"] == "tune" for c in chosen)
    write_json(out / "label_set.json", {
        "created_at": now_iso(), "seed": args.seed, "n": args.n, "bins": bin_info,
        "pool": {"clean_transcripts": len(calls), "recordings": n_rec,
                 "non_short": sum(not c["short_call"] for c in calls)},
        "rubric_params": params,
        "calls": [{"call_id": c["call_id"], "split": c["_split"], "duration_s": c["duration_s"],
                   "bin": c["_bin"]} for c in ordered],
    })
    (out / "LABELLING_GUIDE.md").write_text(guide_text(params, n_tune, len(chosen) - n_tune), encoding="utf-8")

    for b in bin_info:
        log.info("bin %d: %5.1f-%5.1f s, %d calls, picked %d", b["bin"], b["min_s"], b["max_s"],
                 b["calls_in_bin"], b["picked"])
    for split in ("tune", "test"):
        d = sorted(c["duration_s"] for c in chosen if c["_split"] == split)
        log.info("%s: %d calls, median %.0f s, range %.0f-%.0f s", split, len(d), d[len(d) // 2], d[0], d[-1])
    log.info("wrote %s, %d review files, label_set.json, LABELLING_GUIDE.md", csv_path, len(chosen))
    if len(calls) < n_rec:
        log.warning("PARTIAL pool (%d/%d calls): not a valid label set, re-run after the full run",
                    len(calls), n_rec)


if __name__ == "__main__":
    main()
