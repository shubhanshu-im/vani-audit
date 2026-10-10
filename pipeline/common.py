"""Shared paths and helpers for the vani-audit pipeline scripts."""
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# VANI_DATASET picks which calls a run works on: "main" = the 713 hackathon recordings
# (data/, outputs/); any other name, e.g. "audit" = human-audited calls from the IndiaMART
# audit sheet, lives in data/<name>/ and outputs/<name>/ so the two never mix.
DATASET = os.environ.get("VANI_DATASET", "main")
DATA = ROOT / "data" if DATASET == "main" else ROOT / "data" / DATASET
RECORDINGS = DATA / "recordings"
RAW_DIR = DATA / "transcripts" / "raw"
CLEAN_DIR = DATA / "transcripts" / "clean"
TRANSCRIPT_ERRORS = DATA / "transcripts" / "errors.json"
OUTPUTS = ROOT / "outputs" if DATASET == "main" else ROOT / "outputs" / DATASET
TIMELINE = OUTPUTS / "timeline.json"
RUBRIC_MD = ROOT / "docs" / "quality_matrix.md"
# Rubric rows that are not issue parameters a call can be tagged with
NON_PARAM_ROWS = {"Overall Call"}

_file_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request at INFO, including signed upload/download URLs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("vani")


def call_id_of(path: Path) -> str:
    """call_id is the recording filename stem, e.g. 1000_90643234_F0C4W3KG5."""
    return path.name.split(".")[0]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    """Atomic write: a crash mid-write never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def update_json(path: Path, fn, default=None):
    """Read-modify-write under a process-wide lock. fn mutates and/or returns the new value."""
    with _file_lock:
        data = read_json(path, default if default is not None else {})
        result = fn(data)
        data = data if result is None else result
        write_json(path, data)
        return data


def timeline_update(stage: str, **fields) -> None:
    """Merge fields into outputs/timeline.json[stage], leaving other stages untouched."""
    def apply(tl):
        tl.setdefault(stage, {}).update(fields)
    update_json(TIMELINE, apply)


def rubric_params():
    """Parameter names from the first column of the quality matrix table, in order."""
    params = []
    for line in RUBRIC_MD.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 2 or cells[0] in ("Parameter", "") or set(cells[0]) <= set("-:"):
            continue
        if cells[0] not in NON_PARAM_ROWS:
            params.append(cells[0])
    if not params:
        raise SystemExit(f"could not read rubric parameters from {RUBRIC_MD}")
    return params


class RateLimiter:
    """Sliding-window limiter shared by all worker threads."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls = deque()
        self.lock = threading.Lock()

    def acquire(self, cost: int = 1) -> None:
        for _ in range(cost):
            while True:
                with self.lock:
                    now = time.monotonic()
                    while self.calls and now - self.calls[0] >= 60:
                        self.calls.popleft()
                    if len(self.calls) < self.per_minute:
                        self.calls.append(now)
                        break
                    wait_s = 60 - (now - self.calls[0]) + 0.1
                time.sleep(wait_s)


# ------------------------------------------------------------------ Sarvam LLM ----
# One client, one rate limiter and one cache for every script (grader, root causes,
# fixes, simulation). Reasoning is always OFF: on the Starter plan sarvam-105b can spend
# the whole 4096-token max_tokens on reasoning and return empty content.
MODEL_MAIN = os.environ.get("SARVAM_LLM", "sarvam-105b")          # 128K context
MODEL_CHAT = os.environ.get("SARVAM_CHAT_LLM", "sarvam-105b-conversations")  # 32K, dialogue
LLM_RPM = int(os.environ.get("LLM_RPM", "35"))   # account limit is 40/min, shared by the team
LLM_CACHE = ROOT / "outputs" / ".cache"   # shared by all datasets
_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
_llm_state = {}
_llm_lock = threading.Lock()


def _llm_client():
    with _llm_lock:
        if "client" not in _llm_state:
            from dotenv import load_dotenv
            from sarvamai import SarvamAI
            load_dotenv(ROOT / ".env")
            _llm_state["client"] = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
            _llm_state["limiter"] = RateLimiter(LLM_RPM)
        return _llm_state["client"], _llm_state["limiter"]


def chat(messages, model=MODEL_MAIN, temperature=0.0, max_tokens=4096, json_mode=False, cache=None):
    """One chat completion. Returns (text, info). Temperature-0 calls are cached on disk."""
    import hashlib
    import re as _re
    cache = (temperature == 0) if cache is None else cache
    key = hashlib.sha256(json.dumps([model, messages, temperature, max_tokens, json_mode],
                                    ensure_ascii=False).encode()).hexdigest()[:24]
    cpath = LLM_CACHE / f"{key}.json"
    if cache and cpath.exists():
        hit = read_json(cpath)
        return hit["text"], {**hit["info"], "cached": True}

    import httpx
    from sarvamai.core.api_error import ApiError
    client, limiter = _llm_client()
    kw = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
              reasoning_effort=None)
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    delay = 10
    for attempt in range(1, 6):
        limiter.acquire()
        try:
            r = client.chat.completions(**kw)
            break
        except ApiError as e:
            if e.status_code not in _RETRYABLE or attempt == 5:
                raise
            logging.getLogger("vani").warning("LLM HTTP %s, retry in %ds", e.status_code, delay)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == 5:
                raise
            logging.getLogger("vani").warning("LLM %s, retry in %ds", type(e).__name__, delay)
        time.sleep(delay)
        delay = min(delay * 2, 120)
    choice = r.choices[0]
    text = _re.sub(r"<think>.*?</think>", "", choice.message.content or "", flags=_re.S).strip()
    u = getattr(r, "usage", None)
    info = {"model": model, "finish_reason": getattr(choice, "finish_reason", None),
            "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0}
    if cache and text:
        write_json(cpath, {"text": text, "info": info})
    return text, info


def llm(system, user=None, messages=None, **kw):
    """Text reply for a system prompt plus either one user message or a message list."""
    msgs = [{"role": "system", "content": system}] + (messages or [{"role": "user", "content": user}])
    return chat(msgs, **kw)[0]


def parse_json(txt):
    """First JSON object/array in an LLM reply (tolerates code fences and stray prose)."""
    import re as _re
    dec = json.JSONDecoder()
    txt = _re.sub(r"```(?:json)?", "", txt)
    for m in _re.finditer(r"[\[{]", txt):
        try:
            return dec.raw_decode(txt[m.start():])[0]
        except ValueError:
            continue
    raise ValueError("no JSON found in LLM reply")


def llm_json(system, user, retries=3, **kw):
    """Ask for JSON; on a bad reply, retry with the error fed back."""
    system += "\nReply with valid JSON only. No explanation, no markdown."
    msgs = [{"role": "user", "content": user}]
    err = None
    for _ in range(retries):
        text = llm(system, messages=msgs, json_mode=True, **kw)
        try:
            return parse_json(text)
        except ValueError as e:
            err = e
            msgs = msgs[:1] + [{"role": "assistant", "content": text[:4000] or "(empty)"},
                               {"role": "user", "content": f"That was not valid JSON ({e}). Return only the JSON."}]
    raise RuntimeError(f"LLM did not return valid JSON: {err}")


def parallel_map(fn, items, workers=4, label="items"):
    """fn over items in parallel, keeping order; a failing item becomes None instead of crashing."""
    from concurrent.futures import ThreadPoolExecutor
    items, done = list(items), [0]
    log = logging.getLogger("vani")

    def wrap(x):
        try:
            return fn(x)
        except Exception as e:
            log.error("failed on %s: %s", str(x)[:60], e)
            return None
        finally:
            with _llm_lock:
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == len(items):
                    log.info("%s: %d/%d", label, done[0], len(items))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(wrap, items))


# --------------------------------------------------------------- data access ----
GRADES_DIR = OUTPUTS / "grades"
SIGNALS_DIR = OUTPUTS / "signals"
BOT_PROMPT_MD = ROOT / "docs" / "vani_buyer_prompt.md"
PROMPTS_OUT = ROOT / "outputs" / "prompts"   # generated prompt versions (v2 with fixes)


def load_clean(call_id):
    return read_json(CLEAN_DIR / f"{call_id}.json")


def load_grades():
    """{call_id: grade} for every graded call (skips errors.json and partial files)."""
    out = {}
    for p in sorted(GRADES_DIR.glob("*.json")):
        g = read_json(p)
        if isinstance(g, dict) and g.get("grade"):
            out[p.stem] = g
    return out


def fmt_ts(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def transcript_text(clean, max_chars=None):
    """Readable transcript: one '[mm:ss.s] ROLE: text' line per turn."""
    text = "\n".join(f"[{fmt_ts(t['start'])}] {t['role']}: {t['text']}" for t in clean["turns"])
    return text[:max_chars] if max_chars else text


def is_failed(g):
    """A call goes to root-cause analysis if it is FATAL or has any verified issue."""
    return g.get("grade") == "FATAL" or any(i.get("evidence_verified") for i in g.get("issues", []))


def issue_summary(g):
    """One line per call for clustering: failure description plus its issues."""
    issues = "; ".join(f"{i['param']} ({i['severity']}, {i.get('root_cause_type')}): {i.get('reason', '')}"
                       for i in g.get("issues", []) if i.get("evidence_verified"))
    return f"{g.get('failure_description', '').strip()} | {issues}".strip(" |")


def stage_time(stage, field="finished_at"):
    st = (read_json(TIMELINE, {}) or {}).get(stage, {})
    return datetime.fromisoformat(st[field]) if isinstance(st, dict) and st.get(field) else None


def minutes_between(stage_a, stage_b, field_a="finished_at", field_b="finished_at"):
    a, b = stage_time(stage_a, field_a), stage_time(stage_b, field_b)
    return round((b - a).total_seconds() / 60, 1) if a and b else None
