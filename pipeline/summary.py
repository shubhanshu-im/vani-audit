"""Numbers for the submission, slide and demo (from Person 2's summary.py).

    outputs/*.json  ->  outputs/results_summary.md

Usage: .venv/bin/python pipeline/summary.py
"""
from collections import Counter

from common import OUTPUTS, RECORDINGS, load_grades, now_iso, read_json, setup_logging

log = setup_logging()


def main():
    grades = load_grades()
    rank, fx, test, ag = (read_json(OUTPUTS / f) for f in ("ranking.json", "fixes.json", "test_results.json", "agreement.json"))
    n_rec = len(list(RECORDINGS.glob("*.mp3")))
    c = Counter(g["grade"] for g in grades.values())
    n = len(grades)
    talked = c["FATAL"] + c["NON_FATAL"]
    risky = sum(1 for g in grades.values() if g.get("risk_flags"))
    L = [f"# Results summary (auto-generated {now_iso()})\n",
         f"- Calls audited: **{n} of {n_rec} recordings ({100 * n / max(n_rec, 1):.0f}%)**",
         f"- Fatal: **{c['FATAL']}** ({100 * c['FATAL'] / max(talked, 1):.0f}% of {talked} calls with a conversation) "
         f"| Non-Fatal: {c['NON_FATAL']} | No conversation (<10 s): {c['NO_CONVERSATION']}",
         f"- Risky calls flagged for human review: {risky}"]
    if ag and ag.get("agreement_pct") is not None:
        L.append(f"- Agreement with human auditors: **{ag['agreement_pct']}%** (kappa {ag.get('kappa')}, "
                 f"{ag['headline_split']} split, n={ag['by_split'].get(ag['headline_split'], ag['overall'])['n']})")
    else:
        L.append("- Agreement with human auditors: n/a (no human labels yet)")
    if rank:
        L.append(f"\n## Top root causes\n{rank['formula']}\n")
        for r in rank["ranking"][:5]:
            L.append(f"{r['rank']}. {r['name']} [{r['type']}]: {r['n_calls']} calls ({r['frequency_pct']}%), "
                     f"severity {r['severity']}, priority **{r['priority']}**")
    if fx:
        L.append("\n## Prompt fixes")
        L += [f"- {f['cause_name']}: {f['action'].upper()} ({f.get('applied_as')}): {f.get('why')}" for f in fx["fixes"]]
        if fx.get("not_fixable_by_prompt"):
            L.append("- Not fixable by prompt: " + "; ".join(f"{x['name']} [{x['type']}]" for x in fx["not_fixable_by_prompt"]))
    if test:
        s = test["summary"]
        L += ["\n## Fix test", f"- Cause: {s['cause_name']} [{s['cause_type']}]",
              f"- Failure rate (Fatal): **{s['target_failure_before_pct']}% -> {s['target_failure_after_pct']}%** "
              f"({s['target_fatal_before']}/{s['n_target']} -> {s['target_fatal_after']}/{s['n_target']} calls)",
              f"- Cause still visible in the conversation: {s['cause_rate_before_pct']}% -> {s['cause_rate_after_pct']}%",
              f"- Bot exceeded an ask limit (rule-based loop check): {s.get('loop_rate_before_pct')}% -> {s.get('loop_rate_after_pct')}%",
              f"- Held-out calls (not used to write the fix, n={s['n_held_out']}): {s['held_out_before_pct']}% -> {s['held_out_after_pct']}%",
              f"- Regression: {len(s['regression_newly_failing'])} of {s['n_regression']} passing calls now fail",
              f"- Time from root cause found to tested fix: **{s['minutes_issue_to_tested_fix']} min**",
              f"- Caveat: {s['caveat']}"]
    (OUTPUTS / "results_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
