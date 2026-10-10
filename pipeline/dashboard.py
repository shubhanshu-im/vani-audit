"""VANI Quality Audit & Fix Loop dashboard (from Person 2's dashboard.py).

Run from the repo root:  .venv/bin/streamlit run pipeline/dashboard.py
Tabs: Audit (grades, risky-call queue), Issues (ranked root causes), Fix (prompt diffs), Test (before/after).
Every tab shows a friendly message until its input file exists.
"""
import html
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import (OUTPUTS, RECORDINGS, TIMELINE, load_clean, load_grades, read_json,  # noqa: E402
                    transcript_text)

st.set_page_config(page_title="VANI Quality Audit & Fix Loop", layout="wide")


@st.cache_data(ttl=15)
def get_all():
    return dict(grades=load_grades(), rank=read_json(OUTPUTS / "ranking.json"), rc=read_json(OUTPUTS / "root_causes.json"),
                fixes=read_json(OUTPUTS / "fixes.json"), test=read_json(OUTPUTS / "test_results.json"),
                agree=read_json(OUTPUTS / "agreement.json"), tl=read_json(TIMELINE, {}) or {},
                audit_agree=read_json(OUTPUTS / "audit" / "agreement.json"),
                n_rec=len(list(RECORDINGS.glob("*.mp3"))))


def flags_of(g):
    return sorted({f["flag"] for f in g.get("risk_flags", [])})


def render_diff(text):
    rows = []
    for line in text.splitlines():
        e = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            rows.append(f'<div style="background:#12361f;color:#b7f5c9">{e}</div>')
        elif line.startswith("-") and not line.startswith("---"):
            rows.append(f'<div style="background:#4a1a1a;color:#ffc9c9">{e}</div>')
        else:
            rows.append(f'<div style="color:#999">{e}</div>')
    st.markdown('<pre style="white-space:pre-wrap;font-size:13px;padding:8px;border-radius:6px">' + "".join(rows) + "</pre>",
                unsafe_allow_html=True)


def show_conv(turns):
    for role, text in turns:
        st.markdown(f"**{'Bot' if role == 'BOT' else 'Buyer'}:** {text}")


def audio_for(cid):
    p = RECORDINGS / f"{cid}.mp3"
    return str(p) if p.exists() else None


D = get_all()
grades, rank, rc, fixes, test, agree, tl = (D[k] for k in ("grades", "rank", "rc", "fixes", "test", "agree", "tl"))
cause_name = {c["id"]: c["name"] for c in (rc or {}).get("causes", [])}
assign = (rc or {}).get("assignments", {})

with st.sidebar:
    st.header("Pipeline status")
    for label, ok in [("Grades", bool(grades)), ("Agreement", bool(agree and agree.get("agreement_pct") is not None)),
                      ("Root causes", bool(rc)), ("Ranking", bool(rank)), ("Prompt fixes", bool(fixes)),
                      ("Fix test", bool(test)), ("Audio before/after", (OUTPUTS / "audio" / "after.wav").exists())]:
        st.write(("✅ " if ok else "⏳ ") + label)
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

st.title("VANI Quality Audit & Fix Loop")
tabs = st.tabs(["Audit", "Issues", "Fix", "Test", "Live agents"])

# ------------------------------------------------------------------ AUDIT
with tabs[0]:
    n = len(grades)
    if not n:
        st.info("No graded calls yet: run pipeline/grade.py.")
    else:
        cnt = Counter(g["grade"] for g in grades.values())
        risky = [c for c, g in grades.items() if g.get("risk_flags")]
        c = st.columns(5)
        c[0].metric("Calls audited", f"{n} / {D['n_rec']}", help="Share of recordings graded automatically")
        talked = cnt["FATAL"] + cnt["NON_FATAL"]
        c[1].metric("Fatal", f"{cnt['FATAL']} ({100 * cnt['FATAL'] / max(talked, 1):.0f}%)")
        c[2].metric("Non-Fatal", cnt["NON_FATAL"])
        c[3].metric("Risky calls for review", len(risky))
        aa = D.get("audit_agree") or {}
        ag_src = aa if aa.get("agreement_pct") is not None else (agree or {})
        ag = ag_src.get("agreement_pct")
        c[4].metric("Agreement with IndiaMART auditors" if ag_src is aa else "Agreement with humans",
                    f"{ag}%" if ag is not None else "n/a",
                    f"kappa {ag_src.get('kappa')}, n={ag_src.get('by_split', {}).get(ag_src.get('headline_split'), {}).get('fatal_vs_not', {}).get('n', '')}"
                    if ag is not None else "no labels yet", delta_color="off")
        l, r = st.columns(2)
        l.subheader("Quality (auditor scale)")
        qc = Counter(g.get("quality", g["grade"]) for g in grades.values())
        l.bar_chart(pd.Series({"Pass": qc["PASS"], "Non-Fatal": qc["NON_FATAL"], "Fatal": qc["FATAL"],
                               "No conversation": cnt["NO_CONVERSATION"]}))
        params = pd.Series([i["param"] for g in grades.values() for i in g.get("issues", []) if i.get("evidence_verified")])
        r.subheader("Issues by rubric parameter")
        if len(params):
            r.bar_chart(params.value_counts())
        else:
            r.write("No issues yet.")

        st.subheader("Risky calls queue")
        all_flags = sorted({f for g in grades.values() for f in flags_of(g)})
        f1, f2, f3 = st.columns([3, 1, 1])
        pick = f1.multiselect("Filter by flag", all_flags)
        gsel = f2.selectbox("Grade", ["All", "FATAL", "NON_FATAL"])
        cap = f3.slider("Show max", 5, 100, 20)
        rows = [(cid, g) for cid, g in sorted(grades.items())
                if g.get("risk_flags") and (not pick or set(pick) & set(flags_of(g))) and gsel in ("All", g["grade"])]
        st.caption(f"{len(rows)} calls match")
        for cid, g in rows[:cap]:
            with st.expander(f'{cid}  |  {g["grade"]}  |  {", ".join(flags_of(g))}'):
                st.write(f"**Why:** {g.get('grade_reason', '')}")
                if g.get("failure_description"):
                    st.write(g["failure_description"])
                if cid in assign:
                    st.write("**Root cause:** " + cause_name.get(assign[cid], assign[cid]))
                for f in g.get("risk_flags", []):
                    st.caption(f"⚑ {f['flag']}: {f.get('reason', '')} ({f.get('source')})")
                for i in g.get("issues", []):
                    if i.get("evidence_verified"):
                        st.info(f'**{i["param"]}** ({i["severity"]}, {i.get("root_cause_type")}) at {i.get("timestamp")}s: '
                                f'"{i["evidence_quote"]}"  \n{i.get("reason", "")}')
                if audio_for(cid):
                    st.audio(audio_for(cid))
                clean = load_clean(cid)
                if clean:
                    st.code(transcript_text(clean), language=None)

# ------------------------------------------------------------------ ISSUES
with tabs[1]:
    if not rank:
        st.info("Run pipeline/root_causes.py and pipeline/rank.py first.")
    else:
        st.caption(rank["formula"])
        if rank.get("gate"):
            st.info(f"**Importance gate** (only these causes may change the VANI prompt): {rank['gate']['rule']}")
        cols = ["rank", "name", "type", "n_calls", "fatal_calls", "frequency_pct", "severity", "priority",
                "n_batches", "updates_prompt", "gate_reason"]
        df = pd.DataFrame(rank["ranking"])
        df = df[[c for c in cols if c in df.columns]]
        st.bar_chart(df.set_index("name")["priority"])
        st.dataframe(df, hide_index=True)
        for r in rank["ranking"]:
            with st.expander(f'#{r["rank"]}  {r["name"]}  [{r["type"]}]  priority {r["priority"]}'):
                st.write(r["description"])
                st.write(f'Affects **{r["frequency_pct"]}%** of audited calls ({r["n_calls"]}), average severity '
                         f'**{r["severity"]}** (Fatal = 3, Non-Fatal = 1).')
                for cid in r["examples"]:
                    g = grades.get(cid, {})
                    st.write(f"- `{cid}` ({g.get('grade')}): {g.get('failure_description') or g.get('grade_reason', '')}")
                    if audio_for(cid):
                        st.audio(audio_for(cid))

# ------------------------------------------------------------------ FIX
with tabs[2]:
    if not fixes:
        st.info("Run pipeline/prompt_fix.py first.")
    else:
        for f in fixes["fixes"]:
            with st.expander(f'{f["cause_name"]}  ({f["action"].upper()})', expanded=True):
                st.write(f'**Where:** {f.get("where")}  |  **Applied as:** {f.get("applied_as")}  |  **Created:** {f["created_at"]}')
                st.write("**Why:** " + str(f.get("why")))
                st.code(f.get("new_text", ""), language=None)
                st.write("Example calls: " + ", ".join(f["examples"]))
                p = OUTPUTS / f'prompt_diff_{f["cause_id"]}.txt'
                if p.exists():
                    render_diff(p.read_text(encoding="utf-8"))
        st.caption("Rule: fixes change how the bot behaves, never facts, prices or compliance statements (checked automatically).")
        if fixes.get("watchlist"):
            st.subheader("Watchlist: real but not important enough to change the prompt yet")
            for x in fixes["watchlist"]:
                st.write(f'- **{x["name"]}** ({x["n_calls"]} calls): {x["why"]}')
        if fixes.get("not_fixable_by_prompt"):
            st.subheader("Causes a prompt cannot fix")
            for x in fixes["not_fixable_by_prompt"]:
                st.warning(f'**{x["name"]}** [{x["type"]}]: {x["why"]}')

# ------------------------------------------------------------------ TEST
with tabs[3]:
    if not test:
        st.info("Run pipeline/test_fix.py first.")
    else:
        s, res = test["summary"], test["results"]
        st.subheader(f'Tested fix: {s["cause_name"]}')
        c = st.columns(4)
        before, after = s["target_failure_before_pct"], s["target_failure_after_pct"]
        c[0].metric("Failure rate (Fatal)", f"{after}%",
                    f"{after - before:.1f} pts (was {before}%)" if None not in (before, after) else None, delta_color="inverse")
        c[1].metric("Calls fixed", f'{len(s["fixed_calls"])} of {s["n_target"]}')
        c[2].metric("Passing calls now failing", f'{len(s["regression_newly_failing"])} of {s["n_regression"]}')
        c[3].metric("Issue to tested fix", f'{s["minutes_issue_to_tested_fix"]} min' if s["minutes_issue_to_tested_fix"] is not None else "n/a")
        st.write(f'Bot exceeded an ask limit (rule-based check): **{s.get("loop_rate_before_pct")}% → {s.get("loop_rate_after_pct")}%**.  '
                 f'Problem still visible in the conversation: **{s["cause_rate_before_pct"]}% → {s["cause_rate_after_pct"]}%**.  '
                 f'On the {s["n_held_out"]} calls NOT used to write the fix: **{s["held_out_before_pct"]}% → {s["held_out_after_pct"]}%**.')
        st.caption(s["caveat"])
        a1, a2 = st.columns(2)
        for col, k in ((a1, "before"), (a2, "after")):
            p = OUTPUTS / "audio" / f"{k}.wav"
            if p.exists():
                col.write(f"**{k.title()}**")
                col.audio(str(p))
        st.subheader("Compare one call")
        st.dataframe(pd.DataFrame([{"call": r["call_id"], "group": r["group"], "real call": r.get("real_grade"),
                                    "before": r["before"]["grade"], "after": r["after"]["grade"]} for r in res]), hide_index=True)
        sel = st.selectbox("Pick a call", [r["call_id"] for r in res])
        r = next(x for x in res if x["call_id"] == sel)
        with st.expander("Buyer persona (built from the real call)"):
            st.write(r["persona"])
        b, a = st.columns(2)
        b.markdown(f'#### Before: {r["before"]["grade"]}')
        b.caption(r["before"].get("reason", ""))
        a.markdown(f'#### After: {r["after"]["grade"]}')
        a.caption(r["after"].get("reason", ""))
        with b:
            show_conv(r["before"]["turns"])
        with a:
            show_conv(r["after"]["turns"])
    if tl:
        st.subheader("Pipeline timeline")
        st.table(pd.DataFrame([{"stage": k, "started": v.get("started_at", ""), "finished": v.get("finished_at", ""),
                                "status": v.get("status", "")} for k, v in tl.items() if isinstance(v, dict)]))
    if (OUTPUTS / "results_summary.md").exists():
        st.download_button("Download results summary", (OUTPUTS / "results_summary.md").read_text(encoding="utf-8"),
                           "results_summary.md")

# ------------------------------------------------------------------ LIVE AGENTS
with tabs[4]:
    AG = OUTPUTS / "agents"
    agents, evals, chat = (read_json(AG / f) for f in ("agents.json", "eval_results.json", "chat_demo.json"))
    if not agents:
        st.info("No live agents yet: they are created on Sarvam Voice Agents through the MCP (see docs/PROJECT_STATUS.md).")
    else:
        st.subheader("VANI-before vs VANI-after on Sarvam Voice Agents")
        c = st.columns(2)
        for col, k in zip(c, ("before", "after")):
            a = agents["agents"][k]
            col.markdown(f"**{a['name']}** `{a['app_id']}` v{a['version']}")
            col.caption(a["prompt"])
        st.write("**The only difference (approved by Cowork):**")
        st.info(agents["only_difference"])
        if evals:
            st.subheader("Sarvam eval runs (simulated buyers, scored by the platform)")
            rows = []
            for k in ("before", "after"):
                for sc in evals.get(k, {}).get("scenarios", []):
                    rows.append({"agent": k, "scenario": sc["name"], "passed": f"{sc['passed']}/{sc['runs']}",
                                 "behaviours": "; ".join(f"{b['name']}: {b['pass_rate']}" for b in sc.get("behaviours", []))})
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True)
            if evals.get("summary"):
                st.write(evals["summary"])
            if evals.get("iterations"):
                st.subheader("Fix iterations on the target scenario")
                st.dataframe(pd.DataFrame(evals["iterations"]), hide_index=True)
            ex = evals.get("example")
            if ex:
                st.subheader("Same stubborn buyer, both agents (scored eval runs)")
                st.caption(ex.get("note", ""))
                b, a = st.columns(2)
                for col, k in ((b, "before"), (a, "after")):
                    side = ex[k]
                    col.markdown(f"**{side['label']}** ({side['verdict']})")
                    for t in side["transcript"]:
                        col.markdown(f"**{'Bot' if t['role'] == 'assistant' else 'Buyer'}:** {t['content']}")
        if chat:
            st.subheader("Same buyer lines, both agents (text chat)")
            st.caption(chat.get("note", ""))
            for i, line in enumerate(chat["buyer_lines"]):
                st.markdown(f"**Buyer:** {line}")
                b, a = st.columns(2)
                if i < len(chat["before"]):
                    b.markdown(f"*Before:* {chat['before'][i]}")
                if i < len(chat["after"]):
                    a.markdown(f"*After:* {chat['after'][i]}")
            if chat.get("observation"):
                st.warning(chat["observation"])
