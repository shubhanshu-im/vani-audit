"""Deterministic (no-LLM) call signals from clean transcripts.

    data/transcripts/clean/{call_id}.json  ->  outputs/signals/{call_id}.json

Every signal is {"detected": bool, "count": int, "evidence": [{"t", "role", "quote"}], ...}.
Timestamps are the start of the turn containing the quote. Turns are merged STT chunks,
so a long turn's timestamp can be a few seconds before the quoted sentence.

Ask counting works per BOT sentence: a sentence is an "ask" for a field when it
mentions the field and is a question or a request ("bata dijiye", "batayiye", ...).
Spec asks are keyed by the spec name the bot uses ("capacity", "pressure range");
an unnamed follow-up ("aapko kaun sa chahiye?") counts toward the spec asked last.

Usage:
    .venv/bin/python pipeline/signals.py                    # all clean transcripts, skip done
    .venv/bin/python pipeline/signals.py --files ID ... --force
"""
import argparse
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from common import (CLEAN_DIR, OUTPUTS, call_id_of, now_iso, read_json, setup_logging,
                    timeline_update, write_json)

SIGNALS_DIR = OUTPUTS / "signals"
VERSION = 1
MAX_EVIDENCE = 5

# Ask limits from docs/vani_buyer_prompt.md ("Total ask limits")
ASK_LIMITS = {"product": 4, "quantity": 3, "spec": 2, "name": 3, "city": 3, "state": 3,
              "transfer_consent": 2}

log = setup_logging()


def rx(*parts):
    return re.compile("|".join(parts), re.IGNORECASE)


# --- what a BOT sentence is asking for -------------------------------------------------
ASK_MARKER = rx(r"\?", r"बता\s*(दीजिए|दीजिये|दें|दो|सकते|सकती|सकेंगे)", r"बताइए|बताइये|बताएं|बताएँ",
                r"\bplease (tell|share|confirm)", r"\btell me\b", r"\bcould you\b", r"\bcan you\b",
                r"confirm कर", r"prefer करेंगे")
F_PRODUCT = rx(r"कौन\s*सा\s*product", r"requirement\s*(है|थी)\s*ना", r"यही\s*(product\s*)?चाहिए\s*ना",
               r"product\s*चाहिए", r"which product", r"what product", r"क्या आपको .{2,60} की requirement",
               r"क्या आपको .{2,60} चाहिए\s*\?")
F_QUANTITY = rx(r"कितन[ेीा]", r"quantity", r"how many", r"how much")
F_CITY = rx(r"\bcity\b", r"शहर", r"कहा[ँं]\s*से\s*बोल", r"where are you (calling|speaking) from",
            r"से\s*बोल\s*रहे\s*हैं\s*ना")
F_STATE = rx(r"\bstate\b", r"राज्य")
F_NAME = rx(r"(आपका|अपना|your)\s*(पूरा\s*)?(नाम|name)", r"\bname\b", r"नाम\s*(बता|क्या)")
F_TRANSFER = rx(r"connect\s*(कर|करा|करवा)\s*(दूं|दूँ|दू|दें)\s*\?", r"बात\s*(उनसे\s*)?(करवा|करा)\s*(दूं|दूँ|दू)",
                r"connect\s*करना\s*चाहेंगे", r"transfer\s*कर", r"connect you", r"shall i connect",
                r"would you like to (connect|speak)")
SPEC_NAMED = [
    re.compile(r"(?:बताइए|बताइये|चलिए अब|अब)\s+(.{2,40}?)\s+(?:में|के बारे में)\s"),
    re.compile(r"^(.{2,40}?)\s+में\s+(?:आपको\s+)?(?:कौन\s*सा|क्या)\s"),
    re.compile(r"\b(?:which|what)\s+(.{2,30}?)\s+(?:would you|do you)", re.IGNORECASE),
]
F_SPEC = rx(r"prefer\s*करेंगे", r"कौन\s*सा\s*चाहिए", r"(या|or)\s*(कुछ|कोई)\s*और\s*\?", r"or something else",
            r"which one", r"would you prefer", r"के बारे में बात कर लेते")

# --- other BOT patterns ----------------------------------------------------------------
BANNED_Q = rx(r"कोई\s*और\s*(details?|detail|specifications?|जानकारी)", r"any other details?", r"anything else",
              r"any additional requirements?", r"कुछ\s*और\s*(बताना|share)", r"और\s*कुछ\s*(बताना|चाहिए)\s*\?")
CLAIMS_HUMAN = rx(r"मैं\s*(एक\s*)?(इंसान|human|real person|असली)(?!\s*(जैसी|जैसा|की तरह|like))", r"\bi am (a )?(human|real person)\b",
                  r"\bi'?m (a )?(human|real person)\b", r"(मैं|i am|i'm)\s*(कोई\s*)?(robot|bot|ai|machine)\s*(नहीं|not)",
                  r"\bnot (a )?(robot|bot|machine)\b")
BOT_DISCLOSES_AI = rx(r"virtual\s*assistant", r"\bAI\b", r"artificial", r"वर्चुअल")
NOT_UNDERSTOOD = rx(r"आवाज़?\s*clear\s*नहीं", r"आवाज\s*clear\s*नहीं", r"clear\s*नहीं\s*(हो\s*पाया|आई|आया)",
                    r"समझ\s*नहीं\s*(आया|आई|पा\s*रह)", r"could not (hear|understand)", r"couldn'?t (hear|understand)",
                    r"didn'?t (catch|get) that", r"not (able to )?hear you", r"(voice|audio) (is )?not clear")
CLOSING = rx(r"धन्यवाद", r"शुभ\s*हो", r"thank you", r"thanks for", r"have a (great|nice|good) day",
             r"call you (right )?back", r"dhanyavaad", r"sampark", r"संपर्क", r"connect कर रही हूं",
             r"connect कर रहा हूं", r"transfer कर (रही|रहा)", r"line पर बने रहिए")
LANG_LIST_NO_TELUGU = rx(r"सिर्फ\s*(हिंदी|hindi)\s*(या|or|और)\s*(english|अंग्रेज़ी|इंग्लिश)\s*(में|में ही)?\s*(ही\s*)?बात",
                         r"only (in )?hindi (or|and) english")

# --- BUYER patterns --------------------------------------------------------------------
HUMAN_REQUEST = rx(r"इंसान\s*से", r"insaan", r"human\s*से", r"real person", r"असली\s*(आदमी|इंसान|बंदे)",
                   r"किसी\s*(आदमी|व्यक्ति|बंदे)\s*से\s*बात", r"customer\s*care", r"customer\s*support",
                   r"manager\s*से\s*बात", r"executive\s*से\s*बात", r"agent\s*से\s*बात", r"talk to (a )?(person|human|agent)",
                   r"उसी\s*(seller|सेलर|आदमी)\s*से\s*(बात|बात करा)", r"जिस(को|से)\s*call\s*किया\s*था")
AI_QUESTION = rx(r"\brobot\b", r"रोबोट", r"\bbot\b", r"\bAI\b", r"computer\s*(से|है|हो)", r"machine\s*(से\s*बात|हो|है\s*क्या)",
                 r"recorded", r"recording\s*है", r"(तुम|आप)\s*(असली|real)\s*(हो|है)", r"are you (a )?(real|human|robot|bot)")
FRUSTRATION = rx(r"बता\s*(तो\s*)?दिया", r"बोला\s*(तो\s*)?(ना|न|था)", r"बोल\s*तो\s*रहा", r"कितनी\s*बार", r"कितने\s*बार",
                 r"सुनते\s*(ही\s*)?नहीं", r"समझ\s*(में\s*)?नहीं\s*आ\s*रहा", r"बकवास", r"पागल", r"बेवकूफ", r"दिमाग\s*(खराब|मत)",
                 r"time\s*(waste|खराब)", r"टाइम\s*(खराब|वेस्ट)", r"फ़?ोन\s*(रखो|रख\s*दो|काट)", r"phone\s*(रखो|काट)",
                 r"परेशान", r"irritat", r"nonsense", r"already (told|said)", r"i (told|said) you", r"stupid", r"useless",
                 r"faltu|फालतू", r"मज़ाक|मजाक")
SELF_NAME = rx(r"(मेरा|हमारा)\s*नाम(?!\s*क्या)", r"my name is", r"this is \w+ (speaking|here)")
OTHER_LANG_NAME = rx(r"kannada|कन्नड़|tamil|तमिल|malayalam|मलयालम|bengali|bangla|बंगाली|बांग्ला|marathi|मराठी|"
                     r"gujarati|गुजराती|punjabi|पंजाबी|odia|oriya|उड़िया|ओड़िया")
OTHER_SCRIPT = re.compile(r"[ঀ-৿਀-੿઀-૿଀-୿஀-௿ಀ-೿ഀ-ൿ]")
ACKS = {"हाँ", "हां", "हा", "जी", "हाँ जी", "जी हाँ", "ok", "okay", "ओके", "hello", "हेलो", "हैलो", "नहीं", "ना",
        "हम्म", "हम", "अच्छा", "ठीक है", "yes", "no", "yeah", "हाँ हाँ", "जी जी", "हां जी", "बोलिए", "हाँ बोलिए"}

SENT_SPLIT = re.compile(r"(?<=[?।.!])\s+")


def norm(s):
    s = unicodedata.normalize("NFC", s.lower())
    s = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def ev(turn, quote):
    return {"t": turn["start"], "role": turn["role"], "quote": quote.strip()[:240]}


def signal(evidence, detected=None, **extra):
    d = {"detected": bool(evidence) if detected is None else bool(detected), "count": len(evidence),
         "evidence": evidence[:MAX_EVIDENCE]}
    d.update(extra)
    return d


def sentences(turn):
    return [s for s in SENT_SPLIT.split(turn["text"]) if s.strip()]


def spec_name(sent):
    for pat in SPEC_NAMED:
        m = pat.search(sent)
        if m:
            name = norm(m.group(1))
            name = re.sub(r"^((अब|चलिए|आपको|आप|तो|जी|बताइए|बताइये)\s+)+", "", name)
            if name and not re.search(r"\b(quantity|city|state|product|name)\b|कितन|नाम", name):
                return name
    return None


def classify_ask(sent, last_spec):
    """Fields a BOT sentence asks for. Returns (fields, spec key or None)."""
    if not ASK_MARKER.search(sent):
        return [], None
    fields = []
    city, state = F_CITY.search(sent), F_STATE.search(sent)
    if city:
        fields.append("city")
    if state:
        fields.append("state")
    if F_NAME.search(sent) and not (city or state):
        fields.append("name")
    if F_TRANSFER.search(sent):
        fields.append("transfer_consent")
    if F_QUANTITY.search(sent) and not fields:
        fields.append("quantity")
    if F_PRODUCT.search(sent) and not fields:
        fields.append("product")
    spec = None
    if not fields and (F_SPEC.search(sent) or spec_name(sent)) and not BANNED_Q.search(sent):
        spec = spec_name(sent) or last_spec or "unnamed"
        fields.append("spec")
    return fields, spec


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def compute(clean):
    turns = clean["turns"]
    bot = [t for t in turns if t["role"] == "BOT"]
    buyer = [t for t in turns if t["role"] == "BUYER"]

    # ---- ask counting + loops -----------------------------------------------------------
    asks = defaultdict(list)          # field key -> [evidence]
    last_spec, pending_after = None, {}  # turn index -> fields asked in the previous BOT turn
    for i, t in enumerate(turns):
        if t["role"] != "BOT":
            continue
        asked, seen = [], set()
        for s in sentences(t):
            named = spec_name(s)
            if named and not ASK_MARKER.search(s):
                last_spec = named  # topic intro without a question
            fields, spec = classify_ask(s, last_spec)
            for f in fields:
                key = f"spec:{spec}" if f == "spec" else f
                if key not in seen:  # rephrasing within one turn is one ask
                    seen.add(key)
                    asks[key].append(ev(t, s))
                    asked.append(f)
            if spec:
                last_spec = spec
        pending_after[i] = asked
    ask_counts = {k: {"count": len(v), "limit": ASK_LIMITS["spec" if k.startswith("spec:") else k]}
                  for k, v in sorted(asks.items())}
    loop_ev, loop_fields = [], []
    for k, v in asks.items():
        limit = ASK_LIMITS["spec" if k.startswith("spec:") else k]
        if len(v) > limit:
            loop_fields.append({"field": k, "asks": len(v), "limit": limit})
            loop_ev += [v[0], v[limit]]  # first ask and the first over-limit ask
    loop_fields.sort(key=lambda x: -x["asks"])

    # ---- repeated near-identical bot sentences ------------------------------------------
    bot_sents = [(t, s, norm(s)) for t in bot for s in sentences(t)]
    bot_sents = [x for x in bot_sents if len(x[2]) >= 20]
    clusters = []
    for t, s, n in bot_sents:
        for c in clusters:
            if similar(n, c["norm"]) >= 0.8:
                c["hits"].append(ev(t, s))
                break
        else:
            clusters.append({"norm": n, "hits": [ev(t, s)]})
    repeats = [c for c in clusters if len(c["hits"]) >= 2]
    repeats.sort(key=lambda c: -len(c["hits"]))
    rep_ev = [h for c in repeats for h in c["hits"][:2]]

    # ---- buyer repetition (excluding echoes of the bot) ----------------------------------
    echoes, buyer_sents = [], []
    for i, t in enumerate(turns):
        if t["role"] != "BUYER":
            continue
        recent_bot = " ".join(norm(x["text"]) for x in turns[max(0, i - 2):i] if x["role"] == "BOT")
        for s in sentences(t):
            n = norm(s)
            if len(n) >= 8 and recent_bot and (n in recent_bot or similar(n, recent_bot[: len(n) * 2]) >= 0.75):
                echoes.append(ev(t, s))
            elif len(n) >= 8 and n not in {norm(a) for a in ACKS}:
                buyer_sents.append((t, s, n))
    brep = []
    for i, (t, s, n) in enumerate(buyer_sents):
        for t2, s2, n2 in buyer_sents[i + 1:]:
            if t2 is not t and similar(n, n2) >= 0.8:
                brep += [ev(t, s), ev(t2, s2)]
                break

    # ---- dead air -----------------------------------------------------------------------
    noisy = [t["start"] for t in bot if NOT_UNDERSTOOD.search(t["text"])]
    gaps = []
    for g in clean["stats"].get("long_gaps", []):
        noise = any(abs(n - g["end"]) <= 15 for n in noisy)
        threshold = 8.0 if noise else 5.0
        gaps.append({**g, "bot_attributable": g["after_role"] == "BUYER", "noise_context": noise,
                     "threshold_s": threshold, "over_threshold": g["duration_s"] > threshold})
    dead_ev = [{"t": g["start"], "role": "SILENCE", "quote": f"{g['duration_s']:.1f}s silence after {g['after_role']}"}
               for g in gaps if g["bot_attributable"] and g["over_threshold"]]

    # ---- keyword signals ----------------------------------------------------------------
    def scan(role_turns, pattern):
        out = []
        for t in role_turns:
            for s in sentences(t):
                if pattern.search(s):
                    out.append(ev(t, s))
        return out

    human_req = scan(buyer, HUMAN_REQUEST)
    ai_q = scan(buyer, AI_QUESTION)
    frustration = scan(buyer, FRUSTRATION)
    banned = scan(bot, BANNED_Q)
    claims_human = scan(bot, CLAIMS_HUMAN)
    disclosed = scan(bot, BOT_DISCLOSES_AI)
    lang_no_telugu = scan(bot, LANG_LIST_NO_TELUGU)
    not_understood = scan(bot, NOT_UNDERSTOOD)

    # buyer answers a different field than asked, e.g. gives their name when asked for city
    mismatch = []
    for i, t in enumerate(turns):
        if t["role"] == "BUYER" and i > 0 and SELF_NAME.search(t["text"]):
            prev_fields = pending_after.get(i - 1, [])
            if prev_fields and "name" not in prev_fields:
                mismatch.append({**ev(t, t["text"]), "asked": prev_fields})

    # ---- low ASR / language proxies -----------------------------------------------------
    short_garbled = [ev(t, t["text"]) for t in buyer
                     if len(norm(t["text"]).split()) <= 2 and norm(t["text"]) not in {norm(a) for a in ACKS}]
    other_lang = [ev(t, s) for t in buyer for s in sentences(t) if OTHER_SCRIPT.search(s) or OTHER_LANG_NAME.search(s)]
    lang = clean.get("language_code")
    lang_off = lang not in ("hi-IN", "en-IN")

    # ---- abrupt end ---------------------------------------------------------------------
    last_bot = bot[-1] if bot else None
    abrupt = []
    if last_bot and not clean["short_call"] and not CLOSING.search(last_bot["text"]):
        abrupt.append(ev(last_bot, last_bot["text"]))
    last_role = turns[-1]["role"] if turns else None

    return {
        "loop": signal(loop_ev, detected=bool(loop_fields), fields=loop_fields),
        "ask_counts": ask_counts,
        "bot_repetition": signal(rep_ev, detected=bool(repeats),
                                 clusters=[{"times": len(c["hits"]), "quote": c["hits"][0]["quote"]} for c in repeats[:5]]),
        "buyer_repetition": signal(brep),
        "echo_fragments": signal(echoes, detected=False, note="BUYER text that echoes the bot; informational"),
        "dead_air": signal(dead_ev, gaps=gaps),
        "human_request": signal(human_req),
        "ai_identity_question": signal(ai_q, bot_disclosed_ai=bool(disclosed)),
        "frustration": signal(frustration),
        "banned_question": signal(banned),
        "bot_claims_human": signal(claims_human),
        "language_list_omits_telugu": signal(lang_no_telugu),
        "answer_field_mismatch": signal(mismatch),
        "low_asr": {
            "detected": len(not_understood) >= 2 or len(short_garbled) >= 3 or bool(other_lang) or lang_off,
            "bot_not_understood": signal(not_understood, detected=len(not_understood) >= 2),
            "short_garbled_buyer_turns": signal(short_garbled, detected=len(short_garbled) >= 3,
                                                share_of_buyer_turns=round(len(short_garbled) / len(buyer), 2) if buyer else 0.0),
            "other_language": signal(other_lang, language_code=lang, language_code_not_hi_en=lang_off,
                                     detected=bool(other_lang) or lang_off),
        },
        # No closing line. Who ended it is unknown; ending_hint helps the grader decide.
        "abrupt_end": signal(abrupt, last_speaker=last_role,
                             last_bot_line_is_question=bool(last_bot and "?" in last_bot["text"][-3:]),
                             ending_hint=(None if not abrupt else
                                          "bot_waiting_for_answer" if "?" in last_bot["text"][-3:]
                                          else "bot_cut_off_or_stopped")),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="call_ids (default: every clean transcript)")
    ap.add_argument("--force", action="store_true", help="recompute even if output exists")
    args = ap.parse_args()

    ids = sorted(call_id_of(p) for p in CLEAN_DIR.glob("*.json"))
    if args.files:
        wanted = {f.split(".")[0] for f in args.files}
        if wanted - set(ids):
            raise SystemExit(f"no clean transcript for: {sorted(wanted - set(ids))}")
        ids = [i for i in ids if i in wanted]
    todo = [i for i in ids if args.force or not (SIGNALS_DIR / f"{i}.json").exists()]
    log.info("%d calls, %d to compute", len(ids), len(todo))
    if not todo:
        return
    timeline_update("signals", started_at=now_iso(), status="running")
    counts = defaultdict(int)
    for cid in todo:
        clean = read_json(CLEAN_DIR / f"{cid}.json")
        sig = compute(clean)
        out = {"call_id": cid, "version": VERSION, "short_call": clean["short_call"],
               "duration_s": clean["duration_s"], "signals": sig}
        write_json(SIGNALS_DIR / f"{cid}.json", out)
        for k, v in sig.items():
            if isinstance(v, dict) and v.get("detected"):
                counts[k] += 1
    log.info("detected per signal: %s", dict(sorted(counts.items(), key=lambda kv: -kv[1])))
    done = len(list(SIGNALS_DIR.glob("*.json")))
    timeline_update("signals", finished_at=now_iso(), status="complete", calls=done)


if __name__ == "__main__":
    main()
