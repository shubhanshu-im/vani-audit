"""Write small, safe prompt fixes for the top PROMPT-type root causes (from Person 2's 03_prompt_fix.py).

    outputs/ranking.json + docs/vani_buyer_prompt.md  ->  outputs/fixes.json,
    outputs/prompts/vani_prompt_v2_<cause>.md (one fix each), outputs/prompts/vani_prompt_v2.md (all fixes),
    outputs/prompt_diff_<cause>.txt, outputs/prompt_diff.txt

Rules: one small change per cause (add / edit / remove an instruction). A fix may change
behaviour only: it is rejected if it adds numbers, prices or links, may not contain
template syntax, and the run stops if any fact disappears from the prompt. ASR, KNOWLEDGE
and FLOW causes are listed as not fixable by a prompt change.

Usage: .venv/bin/python pipeline/prompt_fix.py [--top 3] [--cause C1 C3]
"""
import argparse
import difflib
import re

from common import (BOT_PROMPT_MD, OUTPUTS, PROMPTS_OUT, SIGNALS_DIR, issue_summary, llm_json, load_clean, load_grades, now_iso,
                    read_json, setup_logging, timeline_update, transcript_text, write_json)

FACT = re.compile(r"[₹$]\s?\d[\d,.]*|\d[\d,.]*\s?%|\b\d{3,}\b|https?://\S+")
NOT_FIXABLE_WHY = {"ASR": "Speech was misheard. A prompt cannot fix speech recognition.",
                   "KNOWLEDGE": "The bot lacks information. It needs new facts from IndiaMART, not a prompt tweak.",
                   "FLOW": "The call logic or state handling is broken. This needs an engineering change.",
                   "OTHER": "Not classified."}
log = setup_logging()


def facts(s):
    return {re.sub(r"\s", "", x) for x in FACT.findall(s)}


def apply_change(prompt, f):
    """Apply one fix; returns (new prompt, how it was applied)."""
    a, old, new, where = f["action"], f.get("old_text", "").strip(), f.get("new_text", "").strip(), f.get("where", "")
    if a == "remove" and old and old in prompt:
        return prompt.replace(old, "", 1), "removed"
    if a == "edit" and old and old in prompt:
        if old in new:  # the new text extends the old rule: safe to replace
            return prompt.replace(old, new, 1), "edited"
        # otherwise never drop the existing rule: add the new rule on its own line right after it
        start = prompt.index(old)
        end = prompt.find("\n", start + len(old))
        end = len(prompt) if end == -1 else end
        bullet = new if new.lstrip().startswith("-") else "- " + new
        return prompt[:end] + "\n\n" + bullet + prompt[end:], "added after the existing rule"
    lines = prompt.splitlines()
    for i, line in enumerate(lines):  # add at the end of the named section
        if line.lstrip().startswith("#") and where and where.lower().strip("# ") in line.lower():
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("#"):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines[j:j] = [""] + new.splitlines()
            return "\n".join(lines) + "\n", f'added under "{line.strip("# ").strip()}"'
    return prompt.rstrip() + "\n\n## Added behaviour rules (v2)\n\n" + new + "\n", "added at the end"


def make_fix(cause, old_prompt, grades):
    examples = []
    for c in cause["examples"]:
        clean = load_clean(c)
        sig = (read_json(SIGNALS_DIR / f"{c}.json") or {}).get("signals", {})
        asks = ", ".join(f"{k} {v['count']}/{v['limit']}" for k, v in sig.get("ask_counts", {}).items())
        examples.append(f"CALL {c}: {issue_summary(grades.get(c, {}))}\nASK COUNTS (asked/limit): {asks or 'n/a'}\n"
                        + (transcript_text(clean, 1500) if clean else ""))
    for attempt in range(2):
        f = llm_json(
            "You improve a voice bot's system prompt (a Jinja template; leave {{ }}, {% %} and @variables alone). "
            "Work in two steps. (1) Find the existing rule(s) in the prompt that should have prevented the problem "
            "below and explain why the bot still failed (rule missing, vague, buried, or contradicted elsewhere). "
            "(2) Propose ONE small, specific change that directly prevents THIS problem in the example calls: "
            "usually make the existing rule explicit and actionable at the moment the bot decides its next reply "
            "(what to check, what to say instead, when to move on). Put it in the section that governs the failing "
            "behaviour (e.g. Collection Control / Quantity Collection / Specification Collection for repeated "
            "questions; Conversation Closing Rules for bad closings), not in an unrelated handler. A good fix is a "
            "short check the bot runs before every reply plus the exact fallback to use. STRICT RULES: change only how the bot behaves; "
            "NEVER add or change facts, prices, numbers, links or compliance statements; do not add template syntax; "
            "do not touch unrelated behaviour. "
            'Return {"existing_rule": "short quote of the rule that should have prevented it, or none", '
            '"why_it_failed": "one sentence", "action": "add|edit|remove", '
            '"where": "exact heading of the prompt section the change belongs in", '
            '"old_text": "exact existing line for edit/remove, else empty", '
            '"new_text": "the new instruction, one to three short bullet lines", "why": "one sentence"}',
            f"CURRENT PROMPT:\n{old_prompt}\n\nPROBLEM: {cause['name']} - {cause['description']}\n"
            f"EXAMPLE CALLS:\n" + "\n\n".join(examples) +
            ("\n\nYour last answer added numbers/facts or template syntax. Do not." if attempt else ""))
        f["action"] = str(f.get("action", "add")).lower()
        if f["action"] not in ("add", "edit", "remove"):
            f["action"] = "add"
        new = str(f.get("new_text", ""))
        if (not new.strip() and f["action"] != "remove") or facts(new) - facts(old_prompt) or re.search(r"\{[{%]", new):
            continue
        return {**f, "cause_id": cause["id"], "cause_name": cause["name"], "examples": cause["examples"],
                "created_at": now_iso()}
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=3, help="fix the top N causes that passed the importance gate")
    ap.add_argument("--cause", nargs="*", help="fix these cause ids instead (any type)")
    args = ap.parse_args()

    old = BOT_PROMPT_MD.read_text(encoding="utf-8")
    rank = (read_json(OUTPUTS / "ranking.json") or {}).get("ranking")
    if not rank:
        raise SystemExit("Run pipeline/root_causes.py and pipeline/rank.py first.")
    # only causes that passed rank.py's importance gate may change the prompt
    prompt_causes = [r for r in rank if r.get("updates_prompt", r["type"] == "PROMPT")]
    # --cause may name any cause (e.g. to try a prompt fix for a FLOW cause); default: top PROMPT causes
    top = [r for r in rank if r["id"] in args.cause] if args.cause else prompt_causes[:args.top]
    not_fixable = [{"id": r["id"], "name": r["name"], "type": r["type"], "priority": r["priority"],
                    "why": NOT_FIXABLE_WHY.get(r["type"], "")} for r in rank if r["type"] != "PROMPT" and r["id"] != "Other"]
    watchlist = [{"id": r["id"], "name": r["name"], "type": r["type"], "n_calls": r["n_calls"],
                  "why": r.get("gate_reason", "")} for r in rank
                 if r["type"] == "PROMPT" and not r.get("updates_prompt", True) and r["id"] != "Other"]
    if not top:
        raise SystemExit("No cause passed the importance gate (see outputs/ranking.json).")
    started, grades = now_iso(), load_grades()
    PROMPTS_OUT.mkdir(parents=True, exist_ok=True)

    fixes, combined = [], old
    for r in top:
        f = make_fix(r, old, grades)
        if not f:
            log.warning("no safe fix produced for %s %s", r["id"], r["name"])
            continue
        one, how = apply_change(old, f)
        f["applied_as"] = how
        (PROMPTS_OUT / f"vani_prompt_v2_{r['id']}.md").write_text(one, encoding="utf-8")
        diff = "\n".join(difflib.unified_diff(old.splitlines(), one.splitlines(), "old (current)",
                                              f"new ({r['id']} fix)", lineterm=""))
        (OUTPUTS / f"prompt_diff_{r['id']}.txt").write_text(diff, encoding="utf-8")
        combined, _ = apply_change(combined, f)
        fixes.append(f)
        log.info("[%s] %s: %s (%s): %s | why: %s", r["id"], r["name"], f["action"].upper(), how,
                 f.get("new_text"), f.get("why"))
    if not fixes:
        raise SystemExit("No fixes created.")
    (PROMPTS_OUT / "vani_prompt_v2.md").write_text(combined, encoding="utf-8")
    (OUTPUTS / "prompt_diff.txt").write_text("\n".join(difflib.unified_diff(
        old.splitlines(), combined.splitlines(), "old (current)", "new (all fixes)", lineterm="")), encoding="utf-8")
    if facts(old) - facts(combined):
        raise SystemExit(f"a fact/number disappeared from the prompt: {sorted(facts(old) - facts(combined))}")
    write_json(OUTPUTS / "fixes.json", {
        "created_at": now_iso(), "base_prompt": str(BOT_PROMPT_MD.name), "fixes": fixes,
        "not_fixable_by_prompt": not_fixable, "watchlist": watchlist,
        "note": "Only PROMPT-type causes are fixed. ASR, KNOWLEDGE and FLOW problems cannot be fixed by changing the prompt."})
    timeline_update("prompt_fix", started_at=started, finished_at=now_iso(), status="complete", fixes=len(fixes))


if __name__ == "__main__":
    main()
