"""Apply the reviewed prompt changes (outputs/cowork_review.json) to the VANI prompt.

    docs/vani_buyer_prompt.md + outputs/cowork_review.json  ->  outputs/prompts/vani_prompt_final.md,
                                                               outputs/prompt_diff_final.txt

Only "keep" / "reword" decisions are applied; each rule is inserted on its own line right after the
paragraph that starts with `insert_after` (never replacing anything). The same safety checks as
prompt_fix.py: no new numbers / prices / links, no template syntax, and no fact may disappear.

Usage: .venv/bin/python pipeline/apply_review.py
"""
import difflib
import re

from common import BOT_PROMPT_MD, OUTPUTS, PROMPTS_OUT, now_iso, read_json, setup_logging, timeline_update
from prompt_fix import facts

log = setup_logging()


def main():
    review = read_json(OUTPUTS / "cowork_review.json")
    if not review:
        raise SystemExit("outputs/cowork_review.json not found")
    old = BOT_PROMPT_MD.read_text(encoding="utf-8")
    new = old
    for d in review["decisions"]:
        if d["decision"] not in ("keep", "reword"):
            log.info("%s: %s (not applied): %s", d["cause_id"], d["decision"], d["reason"][:100])
            continue
        text = d["final_text"].strip()
        if facts(text) - facts(old) or re.search(r"\{[{%]", text):
            raise SystemExit(f"{d['cause_id']}: rule adds numbers/links or template syntax; refusing")
        start = new.find(d["insert_after"])
        if start == -1:
            raise SystemExit(f"{d['cause_id']}: anchor not found: {d['insert_after']!r}")
        end = new.find("\n", start)
        new = new[:end] + "\n\n" + text + new[end:]
        log.info("%s: %s, inserted after %r", d["cause_id"], d["decision"], d["insert_after"][:60])
    if facts(old) - facts(new):
        raise SystemExit("a fact/number disappeared from the prompt")
    PROMPTS_OUT.mkdir(parents=True, exist_ok=True)
    (PROMPTS_OUT / "vani_prompt_final.md").write_text(new, encoding="utf-8")
    diff = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "final (reviewed)", lineterm=""))
    (OUTPUTS / "prompt_diff_final.txt").write_text(diff, encoding="utf-8")
    timeline_update("review_applied", finished_at=now_iso(), status="complete",
                    applied=sum(d["decision"] in ("keep", "reword") for d in review["decisions"]))
    print(diff)


if __name__ == "__main__":
    main()
