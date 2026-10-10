"""Grade every call with sarvam-105b against prompts/grading_spec.md.

    clean transcript + outputs/signals/{call_id}.json  ->  outputs/grades/{call_id}.json

The LLM judges each rubric parameter separately (hit, severity, evidence quote,
timestamp, root-cause type) and returns the inferred captured lead, risk flags, a
short failure description and a confidence. CODE derives the final grade: any
verified FATAL hit -> FATAL, else NON_FATAL. Short calls (<10 s) get NO_CONVERSATION
without an LLM call.

Every hit's quote must appear in the transcript (normalised, fuzzy match allowed);
its timestamp is taken from the matching turn. Invalid JSON, schema errors or
unverifiable quotes trigger a retry with the errors fed back (up to --attempts).
If the last attempt still has unverifiable quotes, those hits are kept but marked
evidence_verified=false, don't count toward the grade, and the call gets needs_review.

Usage:
    .venv/bin/python pipeline/grade.py --files ID ...     # specific calls
    .venv/bin/python pipeline/grade.py --limit 20         # first 20 ungraded
    .venv/bin/python pipeline/grade.py                    # everything ungraded
"""
import argparse
from pathlib import Path
import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import List, Literal, Optional

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from common import (CLEAN_DIR, GRADES_DIR, MODEL_MAIN, OUTPUTS, ROOT, SIGNALS_DIR, call_id_of, chat, now_iso, read_json,
                    rubric_params, setup_logging, timeline_update, update_json, write_json)
from signals import compute as compute_signals, norm

GRADE_ERRORS = GRADES_DIR / "errors.json"
SPEC_PATH = ROOT / "prompts" / "grading_spec.md"
MODEL = MODEL_MAIN
MAX_TOKENS = 4096  # Starter plan cap; reasoning tokens count toward it
# Even reasoning_effort="low" can spend all 4096 tokens reasoning and return nothing, so
# reasoning is off by default; the "analysis" field gives the model room to think instead.
PARAMS = rubric_params()

# Severity rules the code enforces regardless of what the LLM says (see grading_spec.md)
FORCED_SEVERITY = {
    "Outcome Accuracy": "FATAL",  # rubric: "always fatal"; auditors use Non-Fatal in <0.2% of cases
}
RISK_FLAGS = ["wrong_requirement", "frustration", "loop", "low_confidence", "human_request"]

log = setup_logging()


# ---------------------------------------------------------------- LLM output schema ----
VERDICTS = ("PASS", "NON_FATAL", "FATAL", "NA")


class ParamResult(BaseModel):
    """One rubric parameter. `verdict` (as the human auditors use it) is the source of truth;
    hit / severity / assessable are derived from it for the rest of the pipeline."""
    param: str
    verdict: Literal["PASS", "NON_FATAL", "FATAL", "NA"]
    hit: bool
    assessable: bool
    severity: Optional[Literal["FATAL", "NON_FATAL"]] = None
    evidence_quote: str = ""
    timestamp: Optional[float] = None
    root_cause_type: Optional[Literal["PROMPT", "ASR", "KNOWLEDGE", "FLOW"]] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def tolerate_format_slips(cls, d):
        """null text -> "", and enum fields on non-hit parameters are cleared (they carry no meaning)."""
        if isinstance(d, dict):
            d = dict(d)
            v = str(d.get("verdict") or "").strip().upper().replace("-", "_").replace(" ", "_")
            v = {"NONFATAL": "NON_FATAL", "N/A": "NA", "NOT_APPLICABLE": "NA", "NOT_ASSESSED": "NA"}.get(v, v)
            if v not in VERDICTS:  # older answers: derive from hit / severity / assessable
                v = (str(d.get("severity") or "NON_FATAL").upper() if d.get("hit")
                     else "PASS" if d.get("assessable", True) else "NA")
                v = v if v in VERDICTS else "NON_FATAL"
            d["verdict"] = v
            d["hit"] = v in ("FATAL", "NON_FATAL")
            d["severity"] = v if d["hit"] else None
            d["assessable"] = v != "NA"
            for k in ("evidence_quote", "reason"):
                if d.get(k) is None:
                    d[k] = ""
            if not d.get("hit"):
                for k in ("severity", "root_cause_type", "confidence"):
                    d[k] = None
            for k, ok in (("severity", ("FATAL", "NON_FATAL")), ("root_cause_type", ("PROMPT", "ASR", "KNOWLEDGE", "FLOW")),
                          ("confidence", ("high", "medium", "low"))):
                v = d.get(k)
                if isinstance(v, str):
                    v = v.strip().lower() if k == "confidence" else v.strip().upper().replace("-", "_").replace(" ", "_")
                    # "PROMPT/FLOW", "NONFATAL"... -> first valid value contained in it
                    v = v if v in ok else next((o for o in ok if o.replace("_", "") in v.replace("_", "")), None)
                    d[k] = v
            if d.get("hit"):
                # a hit with missing details: conservative defaults (an empty quote stays unverified)
                d["confidence"] = d.get("confidence") or "low"
        return d

    @field_validator("param")
    @classmethod
    def known_param(cls, v):
        if v not in PARAMS:
            raise ValueError(f"unknown param {v!r}; use one of the exact names listed")
        return v

    @model_validator(mode="after")
    def hit_needs_details(self):
        if self.hit:
            if not self.evidence_quote.strip():
                raise ValueError(f"{self.param}: hit=true needs an evidence_quote")
        return self


class SpecValue(BaseModel):
    name: str
    value: str

    @field_validator("name", "value", mode="before")
    @classmethod
    def as_text(cls, v):
        return "" if v is None else str(v)


class Lead(BaseModel):
    product: Optional[str] = None
    quantity: Optional[str] = None
    quantity_unit: Optional[str] = None
    specs: List[SpecValue] = []
    name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None

    @field_validator("product", "quantity", "quantity_unit", "name", "city", "state", mode="before")
    @classmethod
    def blank_is_null(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class RiskFlag(BaseModel):
    flag: Literal["wrong_requirement", "frustration", "loop", "low_confidence", "human_request"]
    reason: str
    timestamp: Optional[float] = None


class LLMGrade(BaseModel):
    analysis: str
    parameters: List[ParamResult]
    captured_lead: Lead
    risk_flags: List[RiskFlag] = []
    failure_description: str
    confidence: Literal["high", "medium", "low"]

    @model_validator(mode="before")
    @classmethod
    def one_entry_per_param(cls, d):
        """Keep the first entry per known parameter; add any the model left out as not assessed."""
        if isinstance(d, dict) and isinstance(d.get("parameters"), list):
            seen, kept = set(), []
            for p in d["parameters"]:
                if isinstance(p, dict) and p.get("param") in PARAMS and p["param"] not in seen:
                    seen.add(p["param"])
                    kept.append(p)
            missing = [n for n in PARAMS if n not in seen]
            if len(missing) > 3:  # mostly missing: a real failure, let the retry handle it
                return d
            kept += [{"param": n, "hit": False, "assessable": False, "reason": "not returned by the grader"}
                     for n in missing]
            d = {**d, "parameters": sorted(kept, key=lambda p: PARAMS.index(p["param"]))}
        return d

    @model_validator(mode="after")
    def every_param_once(self):
        names = [p.param for p in self.parameters]
        missing = [p for p in PARAMS if p not in names]
        if missing or len(names) != len(PARAMS):
            raise ValueError(f"parameters must list each rubric parameter exactly once; missing={missing}")
        return self


OUTPUT_INSTRUCTIONS = """
## Output

Return ONLY a JSON object, no prose:
{
  "analysis": "<= 120 words, written FIRST: buyer's intent and product; what the buyer said for
               quantity / specs / name / location; what the bot read back or confirmed; which fields
               the bot asked repeatedly; how the call ended (closing line -> inferred outcome)",
  "parameters": [  // exactly one entry per parameter, in this order: %(params)s
    {"param": "<exact name>", "verdict": "PASS"|"NON_FATAL"|"FATAL"|"NA",
     "evidence_quote": "<verbatim from transcript; required for NON_FATAL / FATAL>",
     "timestamp": <seconds, the t= of the quoted turn>|null,
     "root_cause_type": "PROMPT"|"ASR"|"KNOWLEDGE"|"FLOW"|null,
     "confidence": "high"|"medium"|"low"|null, "reason": "<= 20 words"}
  ],
  "captured_lead": {"product": str|null, "quantity": str|null, "quantity_unit": str|null,
                    "specs": [{"name": str, "value": str}], "name": str|null, "city": str|null, "state": str|null},
  "risk_flags": [{"flag": "wrong_requirement"|"frustration"|"loop"|"low_confidence"|"human_request",
                  "reason": "<= 15 words", "timestamp": <seconds>|null}],
  "failure_description": "<= 2 short lines: what went wrong and why; empty string if all PASS / NA",
  "confidence": "high"|"medium"|"low"
}

Rules:
- Write "analysis" first and base every parameter judgement on it.
- Judge EVERY parameter separately with one verdict. PASS / NA entries: evidence_quote "",
  root_cause_type and confidence null, reason "" (or a few words on why NA).
- evidence_quote: copy 4-25 consecutive words EXACTLY from ONE transcript line, in its original
  script (Devanagari stays Devanagari). Never translate, transliterate, paraphrase or join lines.
  For Dead air, quote the line just before the silence.
- root_cause_type: PROMPT = prompt instruction missing/wrong; ASR = bot misheard the buyer;
  KNOWLEDGE = bot lacked product/category facts; FLOW = state, sequencing or loop logic.
- captured_lead: what the bot would have recorded, judged from what the bot said back or
  acted on; null when unknown. Keep the buyer's own words for values.
- risk_flags: wrong_requirement (bot captured something different from what the buyer wanted),
  frustration, loop, low_confidence (bot struggled to hear/understand at key moments),
  human_request (buyer asked for a person or the original seller).
- The deterministic signals are hints computed by code; confirm them against the transcript.
- Be concise: the whole answer must fit in about 1500 tokens.
"""


def fmt_t(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def transcript_block(clean):
    return "\n".join(f"[{fmt_t(t['start'])} t={t['start']:.1f}] {t['role']}: {t['text']}" for t in clean["turns"])


def signals_block(sig):
    s = sig["signals"]
    out = {"ask_counts": {k: f"{v['count']}/{v['limit']}" for k, v in s["ask_counts"].items()}}
    for k, v in s.items():
        if k in ("ask_counts", "echo_fragments"):
            continue
        if k == "low_asr":
            sub = {kk: vv["count"] for kk, vv in v.items() if isinstance(vv, dict) and vv.get("detected")}
            if v["detected"]:
                out["low_asr"] = sub
            continue
        if v.get("detected"):
            item = {"count": v["count"], "examples": [f"t={e['t']:.1f} {e['quote'][:100]}" for e in v["evidence"][:2]]}
            for extra in ("fields", "ending_hint", "bot_disclosed_ai"):
                if v.get(extra) is not None:
                    item[extra] = v[extra]
            out[k] = item
    return json.dumps(out, ensure_ascii=False)


def build_messages(spec, clean, sig, note=None):
    system = spec + OUTPUT_INSTRUCTIONS % {"params": json.dumps(PARAMS, ensure_ascii=False)}
    user = (f"Call {clean['call_id']}, duration {clean['duration_s']:.0f}s, detected language "
            f"{clean.get('language_code')}.\n\nDeterministic signals:\n{signals_block(sig)}\n\n"
            f"Transcript (BOT = VANI bot, BUYER = caller; machine STT):\n{transcript_block(clean)}"
            + (f"\n\nNOTE: {note}" if note else ""))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ------------------------------------------------------------- quote verification ----
def find_quote(quote, turns):
    """Return the turn containing the quote (normalised, fuzzy), or None."""
    q = norm(re.sub(r"\.\.\.|…", " ", quote))
    if len(q) < 3:
        return None
    best, best_score = None, 0.0
    for t in turns:
        tn = norm(t["text"])
        if q in tn:
            return t
        m = SequenceMatcher(None, q, tn, autojunk=False).find_longest_match(0, len(q), 0, len(tn))
        score = m.size / len(q)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0.85 else None


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def lead_problems(lead, clean):
    """Multi-digit numbers in the captured lead must appear in the transcript (catches invented
    values like "1500 pouch hour"). Single digits are skipped: buyers say them as words ("ek piece")."""
    text = " ".join(t["text"] for t in clean["turns"])
    seen = {n.replace(",", "") for n in NUMBER.findall(text)}
    values = [("quantity", lead.get("quantity"))] + [(f"specs[{sv['name']}]", sv["value"]) for sv in lead.get("specs", [])]
    out = []
    for field, v in values:
        missing = [n for n in NUMBER.findall(str(v or "")) if len(n) >= 2 and n.replace(",", "") not in seen]
        if missing:
            out.append(f"captured_lead.{field} = {v!r}: number(s) {missing} never appear in the transcript; "
                       "report only what the buyer or bot actually said (use null if unknown)")
    return out


def verify(grade, clean, sig):
    """Attach evidence_verified + verified timestamps; check lead numbers. Returns problems."""
    problems = []
    gaps = sig["signals"]["dead_air"].get("gaps", [])
    for p in grade["parameters"]:
        if not p["hit"]:
            continue
        turn = find_quote(p["evidence_quote"], clean["turns"])
        if turn is None and p["param"] == "Dead air" and p.get("timestamp") is not None:
            if any(abs(g["start"] - p["timestamp"]) <= 3 or abs(g["end"] - p["timestamp"]) <= 3 for g in gaps):
                p["evidence_verified"] = True
                continue
        if turn is None:
            p["evidence_verified"] = False
            problems.append(f"{p['param']}: evidence_quote not found verbatim in any transcript line: "
                            f"{p['evidence_quote'][:80]!r}")
        else:
            p["evidence_verified"] = True
            p["llm_timestamp"], p["timestamp"] = p.get("timestamp"), turn["start"]
    # Unverifiable lead numbers mark the call needs_review but don't trigger a retry:
    # numbers spoken as words would otherwise cost retries for correct answers.
    grade["lead_unverified"] = lead_problems(grade["captured_lead"], clean)
    return problems


# ------------------------------------------------------------------------ grading ----
_SPEC = {}


def grading_spec():
    if "text" not in _SPEC:
        _SPEC["text"] = SPEC_PATH.read_text(encoding="utf-8")
        _SPEC["hash"] = hashlib.sha256(_SPEC["text"].encode()).hexdigest()[:12]
    return _SPEC["text"], _SPEC["hash"]


def grade_clean(clean, sig, attempts=3, cache=True, note=None):
    """Grade one call (clean transcript + signals dict). Returns the grade dict."""
    spec, spec_hash = grading_spec()
    cid = clean["call_id"]
    base = {"call_id": cid, "duration_s": clean["duration_s"], "graded_at": now_iso(), "spec_hash": spec_hash}
    if clean["short_call"]:
        return {**base, "grade": "NO_CONVERSATION", "quality": "NA", "grade_reason": "call under 10 s",
                "model": None, "parameters": [], "issues": [], "fatal_params": [],
                "captured_lead": None, "risk_flags": signal_flags(sig), "failure_description": "",
                "analysis": "", "confidence": "high", "needs_review": False, "attempts": 0}

    messages = build_messages(spec, clean, sig, note)
    usage, last_valid, errors = Counter(), None, []
    for attempt in range(1, attempts + 1):
        content, info = chat(messages, model=MODEL, temperature=0, max_tokens=MAX_TOKENS, json_mode=True, cache=cache)
        usage["prompt_tokens"] += info["prompt_tokens"]
        usage["completion_tokens"] += info["completion_tokens"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        try:
            parsed = LLMGrade.model_validate_json(content).model_dump()
        except ValidationError as e:
            errors = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:8]]
            if info.get("finish_reason") == "length":
                errors.insert(0, "output was cut off by the token limit; be much more concise")
        else:
            last_valid = parsed
            errors = verify(parsed, clean, sig)
            if not errors:
                break
        log.warning("%s attempt %d: %d problem(s): %s", cid, attempt, len(errors), errors[0][:160])
        if attempt < attempts:
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:6000] or "(empty)"},
                {"role": "user", "content": "Your answer has these problems; return the full corrected "
                                            "JSON object only:\n- " + "\n- ".join(errors)}]
    if last_valid is None:
        raise RuntimeError(f"no valid JSON after {attempts} attempts: {errors[:3]}")
    return {**base, **derive(last_valid, sig), "model": MODEL, "attempts": attempt,
            "usage": dict(usage), "unresolved_problems": errors}


def grade_call(cid, attempts=3, cache=True):
    clean = read_json(CLEAN_DIR / f"{cid}.json")
    sig = read_json(SIGNALS_DIR / f"{cid}.json")
    if sig is None:
        raise RuntimeError("no signals file; run pipeline/signals.py first")
    return grade_clean(clean, sig, attempts, cache)


def grade_transcript(clean, attempts=3, note=None):
    """Grade an in-memory transcript (e.g. a simulated call). Signals are computed on the fly.

    `clean` needs call_id and turns [{role: BOT|BUYER, text, start, end}]; the other
    clean-transcript fields are filled in when missing."""
    clean = {"duration_s": 0.0, "language_code": None, "short_call": False, **clean}
    clean.setdefault("stats", {"long_gaps": []})
    sig = {"call_id": clean["call_id"], "short_call": False, "signals": compute_signals(clean)}
    return grade_clean(clean, sig, attempts, note=note)


def rederive(g):
    """Re-apply the decision rule to a stored grade (no LLM call)."""
    if g.get("grade") == "NO_CONVERSATION" or not g.get("parameters"):
        return g
    llm = {k: g.get(k) for k in ("parameters", "captured_lead", "failure_description", "analysis", "confidence",
                                 "lead_unverified")}
    llm["risk_flags"] = [f for f in g.get("risk_flags", []) if f.get("source") == "llm"]
    flags_sig = [f for f in g.get("risk_flags", []) if f.get("source") == "signals"]
    new = derive(llm, {"signals": None}, extra_flags=flags_sig)
    return {**g, **new}


def signal_flags(sig):
    s, flags = sig["signals"], []
    def add(flag, ok, reason, ev):
        if ok:
            flags.append({"flag": flag, "reason": reason, "timestamp": ev[0]["t"] if ev else None, "source": "signals"})
    add("loop", s["loop"]["detected"], "ask limit exceeded: " +
        ", ".join(f"{f['field']} {f['asks']}/{f['limit']}" for f in s["loop"].get("fields", [])), s["loop"]["evidence"])
    add("frustration", s["frustration"]["detected"], "frustration keywords", s["frustration"]["evidence"])
    add("human_request", s["human_request"]["detected"], "asked for a person or the original seller",
        s["human_request"]["evidence"])
    low = s["low_asr"]
    add("low_confidence", low["detected"], "low ASR proxies: " +
        ", ".join(k for k, v in low.items() if isinstance(v, dict) and v.get("detected")),
        low["bot_not_understood"]["evidence"] or low["other_language"]["evidence"])
    return flags


def derive(llm, sig, extra_flags=None):
    """Code-side grade: forced severities, then any verified FATAL hit -> FATAL."""
    params = llm["parameters"]
    for p in params:
        if p["hit"] and p["param"] in FORCED_SEVERITY:
            if p["severity"] != FORCED_SEVERITY[p["param"]]:
                p["llm_severity"] = p["severity"]
            p["severity"] = FORCED_SEVERITY[p["param"]]
    issues = [p for p in params if p["hit"]]
    counted = [p for p in issues if p.get("evidence_verified")]
    # Decision rule (chosen on the audit "tune" split): a FATAL only makes the call FATAL when the
    # grader is highly confident. Medium/low-confidence FATALs still show as issues.
    fatal = [p for p in counted if p["severity"] == "FATAL" and p.get("confidence") == "high"]
    grade = "FATAL" if fatal else "NON_FATAL"
    quality = "FATAL" if fatal else "NON_FATAL" if counted else "PASS"  # the auditors' Overall Quality scale
    reason = (f"{fatal[0]['param']}: {fatal[0]['reason']}" if fatal else
              "no fatal issues" if not counted else f"non-fatal only: {', '.join(p['param'] for p in counted)}")

    flags = [{**f, "source": "llm"} for f in llm["risk_flags"]]
    have = {f["flag"] for f in flags}
    flags += [f for f in (extra_flags if extra_flags is not None else signal_flags(sig)) if f["flag"] not in have]
    return {
        "grade": grade, "quality": quality, "grade_reason": reason,
        "fatal_params": [p["param"] for p in fatal],
        "issues": issues,
        "parameters": params,
        "captured_lead": llm["captured_lead"],
        "risk_flags": flags,
        "failure_description": llm["failure_description"] if counted else "",
        "analysis": llm["analysis"],
        "confidence": llm["confidence"],
        "lead_unverified": llm.get("lead_unverified", []),
        "needs_review": (any(not p.get("evidence_verified") for p in issues) or llm["confidence"] == "low"
                         or bool(llm.get("lead_unverified"))),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="call_ids (default: every clean transcript)")
    ap.add_argument("--limit", type=int, help="only the first N ungraded calls")
    ap.add_argument("--force", action="store_true", help="regrade even if a grade exists")
    ap.add_argument("--workers", type=int, default=6, help="calls graded in parallel")
    ap.add_argument("--attempts", type=int, default=3, help="LLM attempts per call")
    ap.add_argument("--rederive", action="store_true", help="re-apply the decision rule to stored grades (no LLM)")
    ap.add_argument("--spec", type=Path, help="grading spec to use (default prompts/grading_spec.md)")
    ap.add_argument("--no-cache", action="store_true", help="don't reuse cached LLM answers (e.g. for calls that failed)")
    args = ap.parse_args()
    if args.spec:
        global SPEC_PATH
        SPEC_PATH = args.spec.resolve()

    if args.rederive:
        n = 0
        for p in GRADES_DIR.glob("*.json"):
            g = read_json(p)
            if isinstance(g, dict) and g.get("grade"):
                write_json(p, rederive(g))
                n += 1
        log.info("re-derived %d stored grades with the current decision rule", n)
        return

    ids = sorted(call_id_of(p) for p in CLEAN_DIR.glob("*.json"))
    if args.files:
        wanted = {f.split(".")[0] for f in args.files}
        if wanted - set(ids):
            raise SystemExit(f"no clean transcript for: {sorted(wanted - set(ids))}")
        ids = [i for i in ids if i in wanted]
    todo = [i for i in ids if args.force or not (GRADES_DIR / f"{i}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    log.info("%d calls selected, %d to grade", len(ids), len(todo))
    if not todo:
        return

    def start(tl):
        st = tl.setdefault("grade", {})
        st.setdefault("started_at", now_iso())
        st.update(status="running", last_run_started_at=now_iso())
    update_json(OUTPUTS / "timeline.json", start)

    done, failed, grades = 0, 0, Counter()
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="grade") as pool:
        futs = {pool.submit(grade_call, cid, args.attempts, not args.no_cache): cid for cid in todo}
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                g = fut.result()
            except Exception as e:
                failed += 1
                log.error("%s failed: %s", cid, e)
                update_json(GRADE_ERRORS, lambda errs: errs.update(
                    {cid: {"error": f"{type(e).__name__}: {e}"[:500], "at": now_iso()}}))
                continue
            write_json(GRADES_DIR / f"{cid}.json", g)
            update_json(GRADE_ERRORS, lambda errs: errs.pop(cid, None) and None)
            done += 1
            grades[g["grade"]] += 1
            log.info("%s -> %s (%s) [%d/%d]", cid, g["grade"], g["grade_reason"][:80], done, len(todo))

    total = len(list(GRADES_DIR.glob("*.json"))) - (1 if GRADE_ERRORS.exists() else 0)
    n_all = len(list(CLEAN_DIR.glob("*.json")))  # complete means every call, not just this run's selection
    def finish(tl):
        st = tl.setdefault("grade", {})
        st.update(calls_graded=total, calls_total=n_all, updated_at=now_iso(),
                  status="complete" if total >= n_all else "partial")
        if total >= n_all:
            st["finished_at"] = now_iso()
        else:
            st.pop("finished_at", None)
    update_json(OUTPUTS / "timeline.json", finish)
    log.info("graded %d, failed %d this run: %s", done, failed, dict(grades))


if __name__ == "__main__":
    main()
