"""Convert raw Sarvam STT JSON into clean transcripts (contract in CLAUDE.md).

    data/transcripts/raw/{call_id}.json  ->  data/transcripts/clean/{call_id}.json

Role assignment, in two passes:
1. Per speaker: Sarvam's speaker_id is not role-stable, so the bot speaker is the one
   whose lines mention IndiaMART / Tania / requirement / seller (ties -> opening speaker).
2. Per line: diarization of this mono 8 kHz audio often puts lines from both people
   under one speaker_id, or splits the bot's voice in two. So every chunk is then
   re-checked by content: phrases only the bot says ("requirement hai na?", "bata
   dijiye", "aawaz clear nahi aayi", the closing line...) -> BOT; first-person buyer
   talk with no bot phrase ("mujhe ... chahiye") -> BUYER; otherwise keep the speaker's
   role. Echoes (a near-copy of the bot line just before) stay BUYER, like before.
Calls whose speaker-level choice was unclear are printed and marked ambiguous.

Gaps (dead air) are measured between STT chunks *before* merging, so a silence
between two bot chunks (bot asks, buyer stays silent, bot asks again) counts. Chunks
can overlap, so a gap only counts when nobody is speaking.

Usage:
    .venv/bin/python pipeline/normalise.py                      # every raw transcript
    .venv/bin/python pipeline/normalise.py --files 470_90685058_U6YFJ1UG5 ...
"""
import argparse
import re
from collections import Counter
from difflib import SequenceMatcher

from mutagen.mp3 import MP3

from common import (CLEAN_DIR, RAW_DIR, RECORDINGS, ROOT, call_id_of, now_iso, read_json,
                    setup_logging, timeline_update, write_json)

SHORT_CALL_S = 10.0
DEAD_AIR_S = 5.0
BOT_CUES = re.compile(
    r"india\s*mart|इंडिया\s*मार्ट|tania|तानिया|टानिया|requirement|रिक्वायरमेंट|रिक्वार्मेंट|seller|सेलर",
    re.IGNORECASE,
)

# Phrases the bot uses and buyers practically never do (bot persona is female, polite, scripted)
BOT_LINE = re.compile("|".join([
    r"india\s*mart", r"इंडिया\s*मार्ट", r"\b(tania|tanya)\b", r"तानिया|टानिया",
    r"requirement\s*(है|थी)\s*ना", r"request\s*.{0,20}(raised|placed)", r"details?\s*confirm",
    r"बोल\s*रही\s*(हूं|हूँ)", r"सुन\s*रही\s*(हूं|हूँ)", r"करवाऊंगी|करवा\s*दूंगी|करवा\s*दूँगी",
    r"sellers?\s*available", r"best\s*(seller|price)", r"seller\s*(से|ही)\s*(discuss|देंगे|connect)",
    r"क्या\s*आप\s*(अभी\s*)?(भी\s*)?line\s*(पर|पे)", r"are you still there", r"बातचीत\s*(को\s*)?जारी\s*रखना",
    r"चाहिए\s*होंगे|चाहिए\s*होगी", r"prefer\s*करेंगे", r"(बता|confirm\s*कर)\s*दीजिए", r"बताइए\s*please",
    r"आवाज़?\s*clear\s*नहीं", r"समझ\s*नहीं\s*(आया|पा\s*रही)", r"clear\s*नहीं\s*हो\s*पाया",
    r"चुनने\s*के\s*लिए\s*धन्यवाद", r"दिन\s*शुभ\s*हो", r"call\s*करने\s*के\s*लिए\s*धन्यवाद",
    r"कोई\s*और\s*details?", r"note\s*कर\s*(ली|लिया|लूंगी|लूँगी)", r"could you please", r"would you prefer",
    r"कौन\s*सा\s*product\s*चाहिए", r"आप\s*अभी\s*call\s*(पर|पे)\s*हैं", r"के\s*बारे\s*में\s*बात\s*कर\s*लेते",
    r"आपको\s*कौन\s*सा\s*चाहिए", r"आपको\s*कितन[ेीा]\s*.{1,25}\s*चाहिए", r"please\s*बताइए",
    r"what product", r"how many .{1,25} do you need",
]), re.IGNORECASE)
BUYER_LINE = re.compile(r"(^|\s)(मुझे|मुझको|हमको|हमें|हमारा|हमारी|हमारे|मेरा|मेरी|मेरे|मैंने|हम\s*लोग)(\s|$)|"
                        r"\b(i need|i want|my name)\b", re.IGNORECASE)

log = setup_logging()


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]|_", " ", s.lower())).strip()


SENTENCE = re.compile(r"(?<=[?।.!])\s+")
ACK = re.compile(r"^(हाँ|हां|हा|जी|हम्म|हम|ok|okay|ओके|अच्छा|ठीक\s*है|yes|haan|nahi|नहीं|ना|बोलिए|hello|हेलो|हैलो)"
                 r"(\s+(हाँ|हां|जी|जी|हम्म|है|बोलिए))*[।.!]?$", re.IGNORECASE)


def _label(text):
    """BOT / BUYER from content alone, or None when the text gives no clue."""
    if BOT_LINE.search(text) and len(text.split()) >= 3:
        return "BOT"
    if BUYER_LINE.search(text) and not BOT_LINE.search(text):
        return "BUYER"
    return None


def _is_echo(text, prev_bot):
    n = _norm(text)
    return bool(n) and any(n in _norm(p["text"]) or SequenceMatcher(None, n, _norm(p["text"])).ratio() >= 0.75
                           for p in prev_bot)


def assign_line_roles(chunks, bot):
    """Second pass: role per chunk (and per sentence when one chunk mixes both voices).

    Returns (new chunk list, number of chunks whose role changed or that were split)."""
    out, changed = [], 0
    for i, c in enumerate(chunks):
        default = "BOT" if c["speaker"] == bot else "BUYER"
        prev_bot = [p for p in out[-2:] if p["role"] == "BOT"]
        sents = [x for x in SENTENCE.split(c["text"]) if x.strip()]
        labels = [_label(x) for x in sents]
        if len(sents) > 1 and "BOT" in labels and "BUYER" in labels:
            # both voices merged into one chunk: split it, sharing the time by text length
            for k in range(len(labels)):  # unlabelled sentences join the previous labelled one
                labels[k] = labels[k] or (labels[k - 1] if k else next(l for l in labels if l))
            total, t0 = sum(len(x) for x in sents), c["start"]
            groups = []
            for x, l in zip(sents, labels):
                if groups and groups[-1][1] == l:
                    groups[-1][0].append(x)
                else:
                    groups.append([[x], l])
            for g_sents, l in groups:
                txt = " ".join(g_sents)
                dur = (c["end"] - c["start"]) * len(txt) / total
                out.append({**c, "text": txt, "role": l, "start": round(t0, 2), "end": round(t0 + dur, 2), "split": True})
                t0 += dur
            changed += 1
            continue
        label = next((l for l in labels if l), None)
        if label == "BOT" and _is_echo(c["text"], prev_bot):
            role = "BUYER"  # bot audio duplicated onto the buyer track: keep as an echo
        elif label:
            role = label
        elif i == 0 and len(c["text"].split()) > 2:
            role = "BOT"  # the call opens with the bot's predefined message
        elif ACK.match(c["text"].strip()) and out and out[-1]["role"] == "BOT" and not c["text"].strip().endswith("?"):
            role = "BUYER"  # short acknowledgement right after a bot line
        else:
            role = default
        out.append({**c, "role": role})
        changed += role != default
    return out, changed


def audio_duration(call_id: str, fallback: float) -> float:
    try:
        return MP3(RECORDINGS / f"{call_id}.mp3").info.length
    except Exception:
        log.warning("%s: could not read audio duration, using last chunk end", call_id)
        return fallback


def assign_bot_speaker(chunks):
    """Return (bot_speaker_id or None, ambiguity reasons, per-speaker cue counts)."""
    speakers = Counter(c["speaker"] for c in chunks)
    if not speakers:
        return None, ["no speech"], {}
    cues = {s: 0 for s in speakers}
    for c in chunks:
        if BOT_CUES.search(c["text"]):
            cues[c["speaker"]] += 1
    opener = chunks[0]["speaker"]
    ranked = sorted(cues.items(), key=lambda kv: (-kv[1], kv[0] != opener))
    bot = ranked[0][0] if ranked[0][1] > 0 else opener

    reasons = []
    if len(speakers) == 1:
        reasons.append("single speaker")
    if len(speakers) > 2:
        reasons.append(f"{len(speakers)} speakers detected")
    if ranked[0][1] == 0:
        reasons.append("no bot cue words; used opening speaker")
    elif bot != opener:
        reasons.append("cue words point to a speaker who did not open the call")
    if len(ranked) > 1 and ranked[0][1] > 0 and ranked[0][1] == ranked[1][1]:
        reasons.append("cue word tie between speakers")
    return bot, reasons, cues


def normalise(call_id: str):
    raw = read_json(RAW_DIR / f"{call_id}.json")
    entries = ((raw or {}).get("diarized_transcript") or {}).get("entries") or []
    chunks = sorted(
        ({"speaker": str(e.get("speaker_id")), "text": (e.get("transcript") or "").strip(),
          "start": float(e.get("start_time_seconds") or 0), "end": float(e.get("end_time_seconds") or 0)}
         for e in entries),
        key=lambda c: c["start"],
    )
    chunks = [c for c in chunks if c["text"]]
    bot, reasons, cues = assign_bot_speaker(chunks)
    for c in chunks:
        c["role"] = "BOT" if c["speaker"] == bot else "BUYER"
    chunks, line_changes = assign_line_roles(chunks, bot)

    # Diarized chunks often overlap (crosstalk, or bot audio split onto the buyer), so
    # silence is measured from the latest end time seen so far, not the previous chunk's end.
    gaps = []
    if chunks:
        last_end, last_role = chunks[0]["end"], chunks[0]["role"]
        for c in chunks[1:]:
            if c["start"] > last_end:
                gaps.append({"start": round(last_end, 2), "end": round(c["start"], 2),
                             "duration_s": round(c["start"] - last_end, 2), "after_role": last_role})
            if c["end"] >= last_end:
                last_end, last_role = c["end"], c["role"]

    turns = []
    for c in chunks:
        if turns and turns[-1]["role"] == c["role"]:
            turns[-1]["text"] += " " + c["text"]
            turns[-1]["end"] = round(c["end"], 2)
        else:
            turns.append({"role": c["role"], "text": c["text"],
                          "start": round(c["start"], 2), "end": round(c["end"], 2)})

    duration = audio_duration(call_id, chunks[-1]["end"] if chunks else 0.0)
    long_gaps = [g for g in gaps if g["duration_s"] > DEAD_AIR_S]
    clean = {
        "call_id": call_id,
        "file": str((RECORDINGS / f"{call_id}.mp3").relative_to(ROOT)),
        "duration_s": round(duration, 2),
        "language_code": (raw or {}).get("language_code"),
        "short_call": duration < SHORT_CALL_S,
        "turns": turns,
        "stats": {
            "bot_turns": sum(t["role"] == "BOT" for t in turns),
            "buyer_turns": sum(t["role"] == "BUYER" for t in turns),
            "longest_gap_s": max((g["duration_s"] for g in gaps), default=0.0),
            "gaps_over_5s": len(long_gaps),
            "long_gaps": long_gaps,
        },
        "role_assignment": {
            "bot_speaker_id": bot,
            "lines_reassigned_by_content": line_changes,
            "cue_hits_by_speaker": cues,
            "ambiguous": bool(reasons),
            "reasons": reasons,
        },
    }
    write_json(CLEAN_DIR / f"{call_id}.json", clean)
    return clean


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="call_ids to normalise (default: all raw transcripts)")
    args = ap.parse_args()

    ids = sorted(call_id_of(p) for p in RAW_DIR.glob("*.json"))
    if args.files:
        wanted = {f.split(".")[0] for f in args.files}
        missing = wanted - set(ids)
        if missing:
            raise SystemExit(f"no raw transcript for: {sorted(missing)}")
        ids = [i for i in ids if i in wanted]

    started = now_iso()
    timeline_update("normalise", started_at=started, status="running")
    ambiguous = []
    for cid in ids:
        c = normalise(cid)
        if c["role_assignment"]["ambiguous"] and not c["short_call"]:
            ambiguous.append(c)

    if ambiguous:
        print(f"\nAmbiguous role assignment in {len(ambiguous)} non-short call(s):")
        for c in ambiguous:
            ra, first = c["role_assignment"], (c["turns"][0]["text"][:90] if c["turns"] else "")
            print(f"  {c['call_id']}: {'; '.join(ra['reasons'])} | cue hits {ra['cue_hits_by_speaker']} "
                  f"| bot=speaker {ra['bot_speaker_id']} | opening: {first!r}")
    short = sum(1 for cid in ids if read_json(CLEAN_DIR / f"{cid}.json")["short_call"])
    log.info("normalised %d transcripts (%d short calls, %d ambiguous non-short)",
             len(ids), short, len(ambiguous))
    timeline_update("normalise", finished_at=now_iso(), status="complete", calls=len(ids),
                    short_calls=short, ambiguous_roles=len(ambiguous))


if __name__ == "__main__":
    main()
