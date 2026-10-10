"""Export the audit results as files to upload to Sarvam Cowork.

    grades + transcripts + root causes + ranking + fixes  ->  outputs/cowork/

Our pipeline does the measuring (grading, verified quotes, batch learning, importance gate).
Cowork gets analysis-ready files in formats it accepts (Markdown / CSV), split in two:

  attach to the Cowork chat (small, read in full):
    00_INSTRUCTIONS.md          what Cowork should do, and the rules for changing the prompt
    01_summary.md               totals, quality split, ranked root causes, gate result, watchlist
    02_proposed_changes.md      the gated prompt changes with reasons, example calls and diffs
  add to a Cowork knowledge base (evidence it can search):
    calls.csv                   one row per call: grade, issues, root cause
    batches/batch_NN.md         the failed calls of each analysis batch: issues, quotes, transcript
    vani_prompt_current.md      the current VANI prompt (template)
    vani_prompt_updated.md      the prompt with all gated changes applied

Buyer names and phone numbers are masked by default (--no-mask to keep them).

Cowork's knowledge-base parser fails on every .md and .txt file we tried (even small English ones), while
CSV files ingest fine. So the knowledge-base copy of everything is written as CSV in outputs/cowork/kb/
(parts of <= --kb-chunk-kb KB, header repeated): calls, issues (one row per verified issue), transcripts
(one row per turn) and both prompts (one row per paragraph). Upload THAT folder to the knowledge base.

Usage: .venv/bin/python pipeline/export_cowork.py
"""
import argparse
import csv
import re
import shutil

from common import (BOT_PROMPT_MD, OUTPUTS, PROMPTS_OUT, fmt_ts, is_failed, load_clean, load_grades, now_iso,
                    read_json, setup_logging)

OUT = OUTPUTS / "cowork"
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
# "Hello <name> [जी] आपकी/आपको ..." : the bot greets the buyer by name in its opening line
GREETING_NAME = re.compile(r"^(?:hello|hi|नमस्ते|हेलो|हैलो)[,\s]+((?:(?!आप|जी\b)[^\s,।?]+[,\s]+){1,3}?)(?:जी[,\s]+)?(?:आप|आपकी|आपको|आपका)",
                           re.IGNORECASE)
log = setup_logging()


class Masker:
    def __init__(self, enabled):
        self.enabled = enabled

    def names_for(self, g, clean):
        names = set()
        lead = (g or {}).get("captured_lead") or {}
        for part in str(lead.get("name") or "").split():
            if len(part) > 2:
                names.add(part)
        first = next((t["text"] for t in clean["turns"] if t["role"] == "BOT"), "") if clean else ""
        m = GREETING_NAME.match(first)
        if m:
            names.update(p for p in m.group(1).split() if len(p) > 1)
        return names

    def apply(self, text, names):
        if not self.enabled or not text:
            return text or ""
        text = PHONE.sub("[PHONE]", text)
        for n in sorted(names, key=len, reverse=True):
            text = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", "[NAME]", text)
        return text


def issues_of(g):
    return [i for i in g.get("issues", []) if i.get("evidence_verified")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-mask", action="store_true", help="keep buyer names and phone numbers")
    ap.add_argument("--kb-chunk-kb", type=int, default=100, help="max size of each knowledge-base CSV part")
    args = ap.parse_args()
    grades = load_grades()
    rc, rank, fx = (read_json(OUTPUTS / f) for f in ("root_causes.json", "ranking.json", "fixes.json"))
    if not grades or not rc or not rank:
        raise SystemExit("Run grade.py, root_causes.py and rank.py first.")
    agree = read_json(OUTPUTS / "audit" / "agreement.json") or read_json(OUTPUTS / "agreement.json") or {}
    mask = Masker(not args.no_mask)
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "batches").mkdir(parents=True)
    cause_name = {c["id"]: c["name"] for c in rc["causes"]}
    assign = rc["assignments"]

    # ---- calls.csv + batch files (same batches as the batch learning in root_causes.py)
    failed = sorted(c for c, g in grades.items() if is_failed(g))
    size = rc.get("batch_size", 40)
    batch_of = {c: i // size + 1 for i, c in enumerate(failed)}
    with (OUT / "calls.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["call_id", "batch", "duration_s", "quality", "grade", "root_cause", "fatal_params",
                    "issue_params", "failure_description"])
        for cid, g in sorted(grades.items()):
            clean = load_clean(cid)
            names = mask.names_for(g, clean)
            w.writerow([cid, batch_of.get(cid, ""), round(g.get("duration_s") or 0), g.get("quality", ""), g["grade"],
                        cause_name.get(assign.get(cid), assign.get(cid, "")), "; ".join(g.get("fatal_params", [])),
                        "; ".join(f"{i['param']} ({i['severity']})" for i in issues_of(g)),
                        mask.apply(g.get("failure_description", ""), names)])
    for b in sorted(set(batch_of.values())):
        calls = [c for c in failed if batch_of[c] == b]
        md = [f"# Batch {b:02d}: {len(calls)} failed calls\n",
              "Each call: grade, root cause, the verified issues (rubric parameter, severity, timestamp, exact quote), "
              "then the transcript. BOT = VANI bot, BUYER = caller. Names and phone numbers are masked.\n"]
        for cid in calls:
            g, clean = grades[cid], load_clean(cid)
            names = mask.names_for(g, clean)
            md.append(f"## {cid}  |  {g.get('quality', g['grade'])}  |  root cause: "
                      f"{cause_name.get(assign.get(cid), assign.get(cid, '-'))}")
            if g.get("failure_description"):
                md.append(f"**Summary:** {mask.apply(g['failure_description'], names)}")
            for i in issues_of(g):
                md.append(f"- **{i['param']}** ({i['severity']}, {i.get('root_cause_type')}) at "
                          f"{fmt_ts(i.get('timestamp') or 0)}: \"{mask.apply(i['evidence_quote'], names)}\": "
                          f"{mask.apply(i.get('reason', ''), names)}")
            if clean:
                md.append("\n```")
                md += [f"[{fmt_ts(t['start'])}] {t['role']}: {mask.apply(t['text'], names)}" for t in clean["turns"]]
                md.append("```\n")
        (OUT / "batches" / f"batch_{b:02d}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---- summary
    from collections import Counter
    q = Counter(g.get("quality", g["grade"]) for g in grades.values())
    gate = rank.get("gate", {})
    L = [f"# VANI audit summary ({now_iso()[:16]})\n",
         f"- Calls audited automatically: **{len(grades)}** (100% of recordings)",
         f"- Quality (auditor scale): Pass {q['PASS']}, Non-Fatal {q['NON_FATAL']}, Fatal {q['FATAL']}, "
         f"no conversation {q['NA'] + q['NO_CONVERSATION']}",
         f"- Failed calls analysed: {len(failed)}, in {len(set(batch_of.values()))} batches of {size}"]
    if agree.get("agreement_pct") is not None:
        L.append(f"- Agreement with human auditors: {agree['agreement_pct']}% (kappa {agree.get('kappa')}, "
                 f"{agree.get('source', '')})")
    L += [f"\n## Ranked root causes\n{rank['formula']}\n", f"**Importance gate:** {gate.get('rule', 'n/a')}\n",
          "| # | Root cause | Type | Calls | % of calls | Severity | Priority | Batches | Updates prompt? |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rank["ranking"]:
        L.append(f"| {r['rank']} | {r['name']} | {r['type']} | {r['n_calls']} | {r['frequency_pct']} | {r['severity']} | "
                 f"{r['priority']} | {r.get('n_batches', '-')} | {'YES' if r.get('updates_prompt') else 'no: ' + r.get('gate_reason', '')} |")
    for r in rank["ranking"]:
        L.append(f"\n### {r['name']} [{r['type']}]\n{r.get('description', '')}\n\nExample calls: "
                 + ", ".join(f"`{c}`" for c in r["examples"]))
    (OUT / "01_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- proposed changes
    P = ["# Proposed VANI prompt changes\n",
         "Only root causes that passed the importance gate are here. Each change is one small, behaviour-only edit "
         "(no new facts, prices, numbers or links), checked automatically.\n"]
    for f in (fx or {}).get("fixes", []):
        P += [f"## {f['cause_id']}: {f['cause_name']}", f"- **Action:** {f['action']} ({f.get('applied_as')})",
              f"- **Section:** {f.get('where')}", f"- **Existing rule that should have prevented it:** {f.get('existing_rule', '-')}",
              f"- **Why the bot still failed:** {f.get('why_it_failed', '-')}", f"- **Why this change:** {f.get('why')}",
              f"- **Example calls:** {', '.join(f['examples'])}", "\n**New text:**\n", "```", f.get("new_text", ""), "```"]
        diff = OUTPUTS / f"prompt_diff_{f['cause_id']}.txt"
        if diff.exists():
            P += ["\n**Diff:**\n", "```diff", diff.read_text(encoding="utf-8"), "```\n"]
    if (fx or {}).get("watchlist"):
        P.append("\n## Watchlist (real, but not important enough to change the prompt yet)")
        P += [f"- {x['name']} ({x['n_calls']} calls): {x['why']}" for x in fx["watchlist"]]
    if (fx or {}).get("not_fixable_by_prompt"):
        P.append("\n## Not fixable by a prompt change")
        P += [f"- {x['name']} [{x['type']}]: {x['why']}" for x in fx["not_fixable_by_prompt"]]
    test = read_json(OUTPUTS / "test_results.json")
    if test:
        s = test["summary"]
        P += ["\n## Test result so far (simulated replay)",
              f"- Cause tested: {s['cause_name']}; failure rate {s['target_failure_before_pct']}% -> "
              f"{s['target_failure_after_pct']}% (n={s['n_target']}); ask-limit loops {s.get('loop_rate_before_pct')}% -> "
              f"{s.get('loop_rate_after_pct')}%; regressions {len(s['regression_newly_failing'])}/{s['n_regression']}",
              f"- Caveat: {s['caveat']}"]
    (OUT / "02_proposed_changes.md").write_text("\n".join(P) + "\n", encoding="utf-8")

    # ---- prompts
    shutil.copy(BOT_PROMPT_MD, OUT / "vani_prompt_current.md")
    if (PROMPTS_OUT / "vani_prompt_v2.md").exists():
        shutil.copy(PROMPTS_OUT / "vani_prompt_v2.md", OUT / "vani_prompt_updated.md")

    # ---- instructions for Cowork
    (OUT / "00_INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    write_kb(grades, failed, batch_of, cause_name, assign, mask, args.kb_chunk_kb * 1024)
    # one paste-ready message for the Cowork chat (attachments go through the same failing text parser)
    (OUT / "PASTE_INTO_COWORK_CHAT.txt").write_text(
        "\n\n".join((OUT / f).read_text(encoding="utf-8") for f in ("00_INSTRUCTIONS.md", "01_summary.md",
                                                                     "02_proposed_changes.md")), encoding="utf-8")
    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    log.info("wrote %d files to %s: %s", len(files), OUT.relative_to(OUTPUTS.parent), ", ".join(files[:6]) + " ...")


def write_csv_parts(kb, stem, header, rows, limit):
    """Write rows as <stem>_partNN.csv files of at most `limit` bytes each, header repeated."""
    import io
    def encode(r):
        buf = io.StringIO()
        csv.writer(buf).writerow(r)
        return buf.getvalue()
    head, parts, cur, size = encode(header), 0, [], 0
    for r in rows:
        line = encode(r)
        if cur and size + len(line.encode()) > limit:
            parts += 1
            (kb / f"{stem}_part{parts:02d}.csv").write_text(head + "".join(cur), encoding="utf-8")
            cur, size = [], 0
        cur.append(line)
        size += len(line.encode())
    if cur:
        parts += 1
        (kb / f"{stem}_part{parts:02d}.csv").write_text(head + "".join(cur), encoding="utf-8")
    return parts


def prompt_rows(path):
    """One row per paragraph: [paragraph_no, section, text]."""
    section, rows = "", []
    for i, para in enumerate((x.strip() for x in path.read_text(encoding="utf-8").split("\n\n")), 1):
        if not para:
            continue
        if para.startswith("#"):
            section = para.lstrip("# ").strip()
        rows.append([i, section, para])
    return rows


def write_kb(grades, failed, batch_of, cause_name, assign, mask, limit):
    kb = OUT / "kb"
    if kb.exists():
        shutil.rmtree(kb)
    kb.mkdir()
    issues, turns = [], []
    for cid in failed:
        g, clean = grades[cid], load_clean(cid)
        names = mask.names_for(g, clean)
        cause = cause_name.get(assign.get(cid), assign.get(cid, ""))
        for i in issues_of(g):
            issues.append([cid, batch_of[cid], g.get("quality", g["grade"]), cause, i["param"], i["severity"],
                           i.get("root_cause_type") or "", fmt_ts(i.get("timestamp") or 0),
                           mask.apply(i["evidence_quote"], names), mask.apply(i.get("reason", ""), names)])
        for n, t in enumerate((clean or {}).get("turns", []), 1):
            turns.append([cid, batch_of[cid], n, fmt_ts(t["start"]), t["role"], mask.apply(t["text"], names)])
    counts = {
        "calls": write_csv_parts(kb, "calls", *read_calls_csv(), limit),
        "issues": write_csv_parts(kb, "issues", ["call_id", "batch", "call_quality", "root_cause", "rubric_param",
                                                 "severity", "issue_type", "time", "exact_quote", "reason"], issues, limit),
        "transcripts": write_csv_parts(kb, "transcripts", ["call_id", "batch", "turn_no", "time", "speaker", "text"],
                                       turns, limit),
    }
    for path, stem in ((OUT / "vani_prompt_current.md", "prompt_current"), (OUT / "vani_prompt_updated.md", "prompt_updated")):
        if path.exists():
            counts[stem] = write_csv_parts(kb, stem, ["paragraph_no", "section", "text"], prompt_rows(path), limit)
    log.info("knowledge-base CSVs in %s (parts <= %d KB): %s", kb.relative_to(OUTPUTS.parent), limit // 1024, counts)


def read_calls_csv():
    with (OUT / "calls.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


INSTRUCTIONS = """# Task for Cowork: review the proposed VANI prompt changes

You are reviewing an automated quality audit of IndiaMART's VANI buyer-side voice bot. The measuring is already
done by our pipeline: every call was transcribed and graded against IndiaMART's Call Quality rubric with verified
quotes; failed calls were analysed in batches of 40; root causes were ranked by Priority = Frequency x Severity, and
only causes that passed the importance gate (PROMPT-type, >= 10 calls and >= 1.5% of calls, seen in >= 3 batches)
got a proposed prompt change.

## Knowledge base "VANI audit evidence" (CSV files)
- calls_partNN.csv: one row per call (grade, root cause, issues).
- issues_partNN.csv: one row per verified issue (call, rubric parameter, severity, exact quote, time, reason).
- transcripts_partNN.csv: one row per spoken turn (call_id, turn_no, time, speaker BOT/BUYER, text).
- prompt_current_partNN.csv / prompt_updated_partNN.csv: the VANI prompt, one row per paragraph with its section.

## What to do
1. For each proposed change below, look up 2-3 of its example calls in issues and transcripts (filter by call_id)
   and check the change really prevents what happened there. Decide: keep, reword, or drop, with a one-line reason.
2. Do NOT add changes for causes that failed the importance gate or are on the watchlist: one-off problems must
   not change the prompt. ASR / KNOWLEDGE / FLOW causes are not fixable by the prompt.
3. Rules for any wording: change behaviour only; never add or change facts, prices, numbers, links or compliance
   statements; no template syntax ({{ }}, {% %}, @variables); short, in the section that governs the behaviour.
4. Output ONLY: (a) a table: change, decision, reason; (b) for every kept/reworded change, the FINAL text of the
   rule and the section it goes in. Do not rewrite the whole prompt; we apply the approved rules ourselves.
"""


if __name__ == "__main__":
    main()
