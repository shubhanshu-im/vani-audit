"""Rank root causes and decide which ones may update the VANI prompt (the importance gate).

    outputs/root_causes.json + outputs/grades/  ->  outputs/ranking.json

Priority  = Frequency x Severity
Frequency = % of ALL audited calls in the cause
Severity  = average over the cause's calls of FATAL = 3, NON_FATAL = 1

Importance gate: a cause may change the prompt only if ALL hold
  - type PROMPT (ASR / KNOWLEDGE / FLOW problems can't be fixed by instructions)
  - it affects >= --min-calls calls AND >= --min-pct % of audited calls
  - it recurred in >= --min-batches separate batches (not a one-off cluster)
Causes that fail the gate go on the watchlist: tracked, not applied.

Usage: .venv/bin/python pipeline/rank.py [--min-calls 10] [--min-pct 1.5] [--min-batches 3]
"""
import argparse

from common import OUTPUTS, load_grades, now_iso, read_json, setup_logging, timeline_update, write_json

FORMULA = ("Priority = Frequency x Severity. Frequency = % of ALL audited calls in the cause. "
           "Severity = average over the cause's calls of Fatal = 3, Non-Fatal = 1.")
log = setup_logging()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-calls", type=int, default=10)
    ap.add_argument("--min-pct", type=float, default=1.5)
    ap.add_argument("--min-batches", type=int, default=3)
    a = ap.parse_args()
    grades, rc = load_grades(), read_json(OUTPUTS / "root_causes.json")
    if not rc:
        raise SystemExit("Run pipeline/root_causes.py first.")
    total = len(grades)
    gate = {"min_calls": a.min_calls, "min_pct": a.min_pct, "min_batches": a.min_batches, "type": "PROMPT",
            "rule": f"type PROMPT, >= {a.min_calls} calls and >= {a.min_pct}% of audited calls, "
                    f"seen in >= {a.min_batches} batches"}
    rows = []
    for cause in rc["causes"] + [{"id": "Other", "name": "Other", "description": "Did not fit any cause", "type": "OTHER"}]:
        calls = [c for c, v in rc["assignments"].items() if v == cause["id"] and c in grades]
        if not calls:
            continue
        n_fatal = sum(grades[c]["grade"] == "FATAL" for c in calls)
        severity = (3 * n_fatal + (len(calls) - n_fatal)) / len(calls)
        freq = 100 * len(calls) / total
        reasons = []
        if cause["type"] != "PROMPT":
            reasons.append(f"type {cause['type']} is not fixable by the prompt")
        if len(calls) < a.min_calls or freq < a.min_pct:
            reasons.append(f"only {len(calls)} calls ({freq:.1f}%)")
        if cause.get("n_batches", a.min_batches) < a.min_batches:
            reasons.append(f"seen in only {cause.get('n_batches')} batch(es)")
        examples = sorted(calls, key=lambda c: (grades[c]["grade"] != "FATAL", c))[:3]  # fatal examples first
        rows.append({**cause, "n_calls": len(calls), "fatal_calls": n_fatal, "frequency_pct": round(freq, 1),
                     "severity": round(severity, 2), "priority": round(freq * severity, 1), "examples": examples,
                     "updates_prompt": not reasons and cause["id"] != "Other",
                     "gate_reason": "passes the importance gate" if not reasons else "; ".join(reasons)})
    rows.sort(key=lambda r: -r["priority"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    write_json(OUTPUTS / "ranking.json", {"created_at": now_iso(), "formula": FORMULA, "gate": gate,
                                          "total_calls": total, "ranking": rows})
    timeline_update("rank", finished_at=now_iso(), status="complete", causes=len(rows),
                    prompt_updates=sum(r["updates_prompt"] for r in rows))
    log.info("gate: %s", gate["rule"])
    log.info("%2s %6s %6s %5s %4s  cause", "#", "prio", "freq%", "sev", "gate")
    for r in rows:
        log.info("%2d %6.1f %6.1f %5.2f %4s  %s %s [%s] (%s batches)%s", r["rank"], r["priority"], r["frequency_pct"],
                 r["severity"], "YES" if r["updates_prompt"] else "-", r["id"], r["name"], r["type"],
                 r.get("n_batches", "?"), "" if r["updates_prompt"] else f" - {r['gate_reason']}")


if __name__ == "__main__":
    main()
