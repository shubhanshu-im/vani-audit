"""Test a prompt fix by replaying calls with the OLD and the NEW prompt (from Person 2's 04_test_fix.py).

    outputs/fixes.json + root_causes.json + grades  ->  outputs/sim/<cause>__<call>.json, outputs/test_results.json

For each test call:
1. A buyer persona is written from the real transcript (what they wanted, said, language, mood).
2. Two conversations are simulated, BEFORE (current prompt) and AFTER (fixed prompt). The bot
   (sarvam-105b) gets the real prompt template rendered for that call and opens with the real
   call's opening line; the buyer is played by sarvam-105b-conversations.
3. Both conversations are graded by the SAME grader as real calls (grade.grade_transcript),
   and an LLM check asks whether the root cause is still visible.

Test set: the cause's calls (target, FATAL first) + passing calls from other causes
(regression). Held-out = target calls NOT used as examples when writing the fix.
Per-call results are saved, so a stopped run continues where it left off.

Usage: .venv/bin/python pipeline/test_fix.py [C1] [--max-target 10] [--n-reg 10] [--turns 8] [--combined] [--fresh]
"""
import argparse
import hashlib
import random
import re

from bot_prompt import context_for_call, render, split_actions
from common import (BOT_PROMPT_MD, MODEL_CHAT, MODEL_MAIN, OUTPUTS, PROMPTS_OUT, SIGNALS_DIR, chat, is_failed,
                    llm, llm_json, load_clean, load_grades, minutes_between, now_iso, parallel_map, read_json,
                    setup_logging, timeline_update, transcript_text, write_json)
from grade import grade_transcript
from signals import compute as compute_signals

SIM_DIR = OUTPUTS / "sim"
# The simulated buyer tends to add [END] after its first sentence; only accept it once the
# bot has actually closed or transferred the call.
BOT_CLOSED = re.compile(r"धन्यवाद|शुभ\s*हो|thank you|thanks for calling|have a (great|nice|good) day|"
                        r"connect\s*कर\s*(रही|रहा)|transfer\s*कर\s*(रही|रहा)|call you (right )?back", re.IGNORECASE)
log = setup_logging()


def persona(clean):
    return llm("Read this real phone call between IndiaMART's VANI bot and a BUYER. Describe the BUYER as a "
               "role-play persona so an actor can replay the call: the product they want; exactly what they said "
               "about quantity, specifications, name and city (keep their words); their language and speaking style "
               "(Hindi / English / Hinglish / other); their mood; how they reacted to the bot. Describe only the "
               "buyer, not the bot's mistakes. Max 150 words.", transcript_text(clean, 6000), temperature=0)


def buyer_script(clean, n=15):
    """The buyer's real lines, so the replay reproduces what actually confused the bot."""
    lines = [t["text"] for t in clean["turns"] if t["role"] == "BUYER"][:n]
    return "\n".join(f"{i}. {t[:200]}" for i, t in enumerate(lines, 1))


def simulate(bot_system, opening, buyer_persona, script, turns):
    """Returns ([(role, text), ...] starting with the bot's real opening line, call_ended)."""
    hist = [("BOT", opening)]
    for _ in range(turns):
        caller_msgs = [{"role": "user" if r == "BOT" else "assistant", "content": t} for r, t in hist]
        reply = llm("You are a BUYER on a phone call with IndiaMART's help desk bot, REPLAYING a real call. "
                    "Stay in character:\n" + buyer_persona + "\n\nWhat you actually said in the real call, in order "
                    "(this is a noisy phone transcript):\n" + script + "\n\nReuse these lines, word for word and in "
                    "order, whenever they fit what the bot just said, even if they are unclear, garbled or off-topic: "
                    "that is exactly what the bot must cope with. Improvise only when the bot asks something new, and "
                    "then answer like this buyer would. Reply with only what you say aloud: 1-2 short sentences. Only when the bot has said "
                    "goodbye / thank you for calling or is transferring you, end your message with [END]; never "
                    "otherwise.",
                    messages=caller_msgs, model=MODEL_CHAT, temperature=0.5, max_tokens=300)
        done = "[END]" in reply and bool(BOT_CLOSED.search(hist[-1][1]))
        reply = reply.replace("[END]", "").strip()
        if reply:
            hist.append(("BUYER", reply))
        if done or not reply:
            return hist, True
        bot_msgs = [{"role": "user", "content": "(call connected)"}] + [
            {"role": "assistant" if r == "BOT" else "user", "content": t} for r, t in hist]
        text, info = chat([{"role": "system", "content": bot_system}] + bot_msgs, model=MODEL_MAIN,
                          temperature=0, max_tokens=600)
        spoken, actions, ended = split_actions(text)
        hist.append(("BOT", spoken or f"[{', '.join(actions) or 'no reply'}]"))
        if ended:
            return hist, True
    return hist, False


def as_clean(call_id, hist):
    return {"call_id": call_id, "turns": [{"role": r, "text": t, "start": 5.0 * i, "end": 5.0 * i + 4}
                                          for i, (r, t) in enumerate(hist)]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cause", nargs="?", help="cause id (default: the first fixed cause)")
    ap.add_argument("--max-target", type=int, default=10, help="calls from the cause")
    ap.add_argument("--n-reg", type=int, default=10, help="passing calls from other causes (regression check)")
    ap.add_argument("--turns", type=int, default=8, help="max buyer+bot exchanges per simulation")
    ap.add_argument("--combined", action="store_true", help="test all fixes together (vani_prompt_v2.md)")
    ap.add_argument("--fresh", action="store_true", help="ignore saved per-call results")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    fx, rc, grades = read_json(OUTPUTS / "fixes.json"), read_json(OUTPUTS / "root_causes.json"), load_grades()
    if not fx or not rc:
        raise SystemExit("Run pipeline/root_causes.py, rank.py and prompt_fix.py first.")
    cause_id = args.cause or fx["fixes"][0]["cause_id"]
    fix = next((f for f in fx["fixes"] if f["cause_id"] == cause_id), None)
    if not fix:
        raise SystemExit(f"No fix for {cause_id}. Fixes exist for: {[f['cause_id'] for f in fx['fixes']]}")
    cause = next(c for c in rc["causes"] if c["id"] == cause_id)
    old_t = BOT_PROMPT_MD.read_text(encoding="utf-8")
    new_t = (PROMPTS_OUT / ("vani_prompt_v2.md" if args.combined else f"vani_prompt_v2_{cause_id}.md")).read_text(encoding="utf-8")
    oh, nh = (hashlib.sha256(t.encode()).hexdigest()[:10] for t in (old_t, new_t))

    target = sorted((c for c, v in rc["assignments"].items() if v == cause_id and load_clean(c)),
                    key=lambda c: (grades.get(c, {}).get("grade") != "FATAL", c))[:args.max_target]
    passing = sorted(c for c, g in grades.items() if g["grade"] == "NON_FATAL" and not is_failed(g))
    if len(passing) < args.n_reg:  # few fully clean calls: add non-fatal calls from other causes
        passing += sorted(c for c, g in grades.items() if g["grade"] == "NON_FATAL" and c not in passing
                          and rc["assignments"].get(c) != cause_id)
    random.Random(3).shuffle(passing)
    regression = passing[:args.n_reg]
    log.info("testing %s '%s': %d target + %d regression calls, prompt %s -> %s",
             cause_id, cause["name"], len(target), len(regression), oh, nh)
    if not target:
        raise SystemExit("No calls with transcripts for this cause.")
    started = now_iso()

    def shows_cause(text):
        try:
            r = llm_json(f"Does this conversation show this problem: {cause['name']} - {cause['description']}? "
                         'Return {"shows_problem": true or false}', text, temperature=0)
            return bool(r.get("shows_problem"))
        except Exception:
            return None

    def process(item):
        cid, group = item
        path = SIM_DIR / f"{cause_id}__{cid}.json"
        saved = None if args.fresh else read_json(path)
        if saved and saved.get("old_hash") == oh and saved.get("new_hash") == nh:
            return saved
        clean = load_clean(cid)
        sig = (read_json(SIGNALS_DIR / f"{cid}.json") or {}).get("signals")
        ctx = context_for_call(clean, sig, (grades.get(cid) or {}).get("captured_lead"))
        per = persona(clean)
        row = {"call_id": cid, "group": group, "cause_id": cause_id, "persona": per, "context": ctx,
               "real_grade": grades.get(cid, {}).get("grade"), "old_hash": oh, "new_hash": nh}
        for name, template in (("before", old_t), ("after", new_t)):
            hist, ended = simulate(render(template, ctx), ctx["initial_message"], per, buyer_script(clean), args.turns)
            sim_clean = as_clean(f"{cid}_{name}", hist)
            note = None if ended else (
                f"This is a SIMULATED call that the test harness cut off after {args.turns} exchanges; the call did "
                "not end. Do NOT judge Outcome Accuracy, closing or abrupt ending; judge only the turns shown.")
            g = grade_transcript(sim_clean, note=note)
            loop = compute_signals({**sim_clean, "duration_s": 0.0, "short_call": False, "language_code": None,
                                    "stats": {"long_gaps": []}})["loop"]
            row[name] = {"turns": [list(x) for x in hist], "ended": ended, "grade": g["grade"],
                         "reason": g["grade_reason"], "fatal_params": g["fatal_params"],
                         "failure_description": g["failure_description"],
                         "loop": loop["detected"], "loop_fields": loop.get("fields", [])}
            if group == "target":
                row[name]["shows_cause"] = shows_cause("\n".join(f"{r}: {t}" for r, t in hist))
        write_json(path, row)
        log.info("%s [%s] %s -> %s", cid, group, row["before"]["grade"], row["after"]["grade"])
        return row

    items = [(c, "target") for c in target] + [(c, "regression") for c in regression]
    results = [r for r in parallel_map(process, items, workers=args.workers, label="simulated calls") if r]
    timeline_update("test_fix", started_at=started, finished_at=now_iso(), status="complete", cause=cause_id,
                    calls=len(results))

    tg = [r for r in results if r["group"] == "target"]
    rg = [r for r in results if r["group"] == "regression"]
    held = [r for r in tg if r["call_id"] not in fix.get("examples", [])]
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    fatal = lambda rs, k: sum(r[k]["grade"] == "FATAL" for r in rs)
    shown = lambda rs, k: sum(1 for r in rs if r[k].get("shows_cause"))
    looped = lambda rs, k: sum(1 for r in rs if r[k].get("loop"))
    summary = {
        "cause_id": cause_id, "cause_name": cause["name"], "cause_type": cause["type"],
        "bot_model": MODEL_MAIN, "buyer_model": MODEL_CHAT, "prompt_hash_old": oh, "prompt_hash_new": nh,
        "fix_applies": "all fixes combined" if args.combined else "this cause's fix only",
        "n_target": len(tg), "target_fatal_before": fatal(tg, "before"), "target_fatal_after": fatal(tg, "after"),
        "target_failure_before_pct": pct(fatal(tg, "before"), len(tg)),
        "target_failure_after_pct": pct(fatal(tg, "after"), len(tg)),
        "cause_shown_before": shown(tg, "before"), "cause_shown_after": shown(tg, "after"),
        "cause_rate_before_pct": pct(shown(tg, "before"), len(tg)), "cause_rate_after_pct": pct(shown(tg, "after"), len(tg)),
        "loop_rate_before_pct": pct(looped(tg, "before"), len(tg)), "loop_rate_after_pct": pct(looped(tg, "after"), len(tg)),
        "fixed_calls": [r["call_id"] for r in tg if r["before"]["grade"] == "FATAL" and r["after"]["grade"] != "FATAL"],
        "still_failing": [r["call_id"] for r in tg if r["after"]["grade"] == "FATAL"],
        "n_held_out": len(held), "held_out_before_pct": pct(fatal(held, "before"), len(held)),
        "held_out_after_pct": pct(fatal(held, "after"), len(held)),
        "n_regression": len(rg), "regression_fatal_before": fatal(rg, "before"), "regression_fatal_after": fatal(rg, "after"),
        "regression_newly_failing": [r["call_id"] for r in rg if r["before"]["grade"] != "FATAL" and r["after"]["grade"] == "FATAL"],
        "minutes_issue_to_tested_fix": minutes_between("root_causes", "test_fix"),
        "minutes_fix_created_to_tested": minutes_between("prompt_fix", "test_fix"),
        "caveat": ("Buyers are simulated by an LLM from the real call, not real people; the bot is the real prompt "
                   "run on sarvam-105b in text, without ASR/TTS or real tool calls."
                   + (f" Very small sample ({len(tg)} calls): treat the rates with care." if len(tg) < 5 else "")),
    }
    write_json(OUTPUTS / "test_results.json", {"created_at": now_iso(), "summary": summary, "results": results})
    log.info("failure rate %s%% -> %s%% (%d calls) | cause visible %s%% -> %s%% | ask-limit loops %s%% -> %s%% | "
             "regressions %d/%d | %s min issue->tested",
             summary["target_failure_before_pct"], summary["target_failure_after_pct"], len(tg),
             summary["cause_rate_before_pct"], summary["cause_rate_after_pct"],
             summary["loop_rate_before_pct"], summary["loop_rate_after_pct"],
             len(summary["regression_newly_failing"]), len(rg), summary["minutes_issue_to_tested_fix"])


if __name__ == "__main__":
    main()
