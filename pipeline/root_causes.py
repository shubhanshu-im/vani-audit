"""Batch learning: find the important, recurring reasons calls fail.

    outputs/grades/*.json  ->  outputs/root_causes.json, outputs/root_causes_review.md

1. MAP: failed calls (FATAL, or with any verified issue) are split into batches of
   --batch-size, in call order. For each batch the LLM extracts up to 6 patterns, each
   with the calls that show it, typed PROMPT | ASR | KNOWLEDGE | FLOW.
2. REDUCE: all batch patterns are merged into 5-12 canonical root causes.
3. SUPPORT: for every cause we count its calls, FATAL calls and the number of batches it
   appeared in. rank.py then applies the importance gate: only causes that recur across
   batches and affect enough calls may change the VANI prompt; the rest go on a watchlist.
   One odd call never updates the prompt.

Hand fixes: put {"<call_id>": "C2", ...} in outputs/root_causes_overrides.json and re-run.

Usage: .venv/bin/python pipeline/root_causes.py [--batch-size 40]
"""
import argparse
from collections import Counter, defaultdict

from common import (OUTPUTS, issue_summary, is_failed, llm_json, load_grades, now_iso, parallel_map, read_json,
                    setup_logging, timeline_update, write_json)

TYPES = ("PROMPT", "ASR", "KNOWLEDGE", "FLOW")
log = setup_logging()

CONTEXT = (
    "You analyse failures of IndiaMART's VANI buyer-side voice bot (Hindi/English/Hinglish). The bot confirms a "
    "buyer's product, collects quantity, specs, name and city, then connects a live seller or creates a buy lead. "
    "The bot is an LLM driven entirely by its system prompt, so its conversation behaviour (what it asks, how often "
    "it re-asks, when it moves on or closes, how it handles language, objections and unclear answers) is set by the "
    "prompt. type must be exactly one of: PROMPT (the bot did something its instructions could tell it to do "
    "differently), ASR (the bot misheard / mis-transcribed the buyer's words), KNOWLEDGE (the bot lacked product or "
    "category facts), FLOW (a problem outside the prompt: telephony drop, transfer/tool failure, wrong variables or "
    "data passed in). Name causes by the underlying reason in simple words a non-technical person understands "
    "(e.g. 'Bot keeps asking city when it can't understand the answer'), not by the symptom ('Looping').")

MAP = (CONTEXT + " Read this batch of failed calls and list up to 6 distinct problem PATTERNS in it, most important "
       "first. Only list a pattern if you can name the calls that show it. "
       'Return {"patterns": [{"name": "", "description": "one line", "type": "PROMPT", "calls": ["<call_id>", ...]}]}')

REDUCE = (CONTEXT + " Below are problem patterns found independently in several batches of calls. Merge patterns "
          "that describe the same underlying cause into 5-12 ROOT CAUSES. Every pattern key must belong to exactly "
          "one cause. "
          'Return {"causes": [{"name": "", "description": "one line", "type": "PROMPT", "patterns": ["b1p1", ...]}]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-size", type=int, default=40)
    args = ap.parse_args()

    grades = load_grades()
    failed = {c: g for c, g in sorted(grades.items()) if is_failed(g)}
    log.info("%d graded calls, %d failed (FATAL or with verified issues)", len(grades), len(failed))
    if not failed:
        raise SystemExit("No failures yet: run pipeline/grade.py first.")
    started = now_iso()
    ids = list(failed)
    batches = [ids[i:i + args.batch_size] for i in range(0, len(ids), args.batch_size)]

    # 1. MAP: patterns per batch
    def map_batch(item):
        b, calls = item
        r = llm_json(MAP, "\n".join(f"- {c}: {issue_summary(failed[c])}" for c in calls))
        pats = r.get("patterns", []) if isinstance(r, dict) else r
        out = []
        for i, p in enumerate(pats[:6], 1):
            members = [c for c in p.get("calls", []) if c in failed and c in calls]
            if members:
                t = str(p.get("type", "")).upper()
                out.append({"key": f"b{b}p{i}", "batch": b, "name": str(p.get("name", "")).strip(),
                            "description": str(p.get("description", "")).strip(),
                            "type": t if t in TYPES else "OTHER", "calls": members})
        return out
    patterns = [p for res in parallel_map(map_batch, list(enumerate(batches, 1)), workers=4, label="map batches")
                if res for p in res]
    log.info("map: %d patterns from %d batches", len(patterns), len(batches))

    # 2. REDUCE: merge into canonical causes
    by_key = {p["key"]: p for p in patterns}
    r = llm_json(REDUCE, "\n".join(f"- {p['key']} [{p['type']}] ({len(p['calls'])} calls): {p['name']} - {p['description']}"
                                   for p in patterns))
    raw = r.get("causes", []) if isinstance(r, dict) else r
    causes, pattern_cause = [], {}
    for c in raw:
        keys = [k for k in c.get("patterns", []) if k in by_key and k not in pattern_cause]
        if not keys:
            continue
        cid = f"C{len(causes) + 1}"
        for k in keys:
            pattern_cause[k] = cid
        t = str(c.get("type", "")).upper()
        causes.append({"id": cid, "name": str(c.get("name") or f"Cause {cid}").strip(),
                       "description": str(c.get("description", "")).strip(), "type": t if t in TYPES else "OTHER",
                       "patterns": keys})
    for p in patterns:  # patterns the merge forgot become their own cause
        if p["key"] not in pattern_cause:
            cid = f"C{len(causes) + 1}"
            pattern_cause[p["key"]] = cid
            causes.append({"id": cid, "name": p["name"], "description": p["description"], "type": p["type"],
                           "patterns": [p["key"]]})

    # 3. SUPPORT: calls and batches per cause; each call's primary cause = its most supported cause
    call_causes = defaultdict(set)
    for p in patterns:
        for c in p["calls"]:
            call_causes[c].add(pattern_cause[p["key"]])
    for c in causes:
        members = {m for k in c["patterns"] for m in by_key[k]["calls"]}
        c.update(n_calls=len(members), fatal_calls=sum(grades[m]["grade"] == "FATAL" for m in members),
                 n_batches=len({by_key[k]["batch"] for k in c["patterns"]}),
                 batch_names=sorted({by_key[k]["name"] for k in c["patterns"]})[:6])
    size = {c["id"]: c["n_calls"] for c in causes}
    assignments = {c: (max(call_causes[c], key=lambda x: (size[x], x)) if call_causes.get(c) else "Other") for c in failed}
    overrides = read_json(OUTPUTS / "root_causes_overrides.json", {}) or {}
    ids_ok = {c["id"] for c in causes}
    for c, v in overrides.items():
        if c in assignments and (v in ids_ok or v == "Other"):
            assignments[c] = v
    if overrides:
        log.info("applied %d manual overrides", len(overrides))

    write_json(OUTPUTS / "root_causes.json", {
        "created_at": now_iso(), "method": "batch map-reduce", "batch_size": args.batch_size, "n_batches": len(batches),
        "n_audited": len(grades), "n_failed": len(failed), "causes": causes, "patterns": patterns,
        "assignments": assignments, "call_causes": {c: sorted(v) for c, v in call_causes.items()}})

    md = ["# Root causes (batch learning): read 3 calls per cause. Do they really belong together?\n"]
    for c in sorted(causes, key=lambda c: -c["n_calls"]):
        calls = sorted(k for k, v in assignments.items() if v == c["id"])
        md.append(f'## {c["id"]}: {c["name"]}  [{c["type"]}]  ({c["n_calls"]} calls, {c["fatal_calls"]} fatal, '
                  f'seen in {c["n_batches"]}/{len(batches)} batches)\n{c["description"]}\n')
        md += [f"- `{k}`: {issue_summary(failed[k])}" for k in calls[:3]] + [""]
    (OUTPUTS / "root_causes_review.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    timeline_update("root_causes", started_at=started, finished_at=now_iso(), status="complete",
                    failed_calls=len(failed), causes=len(causes), batches=len(batches))
    for c in sorted(causes, key=lambda c: -c["n_calls"]):
        log.info("%s [%s] %s: %d calls (%d fatal), %d/%d batches", c["id"], c["type"], c["name"], c["n_calls"],
                 c["fatal_calls"], c["n_batches"], len(batches))
    log.info("Other: %d calls", Counter(assignments.values())["Other"])


if __name__ == "__main__":
    main()
