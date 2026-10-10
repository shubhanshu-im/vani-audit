"""Voice one before/after simulated call with Sarvam Bulbul (from Person 2's 05_voice.py).

    outputs/test_results.json  ->  outputs/audio/before.wav, outputs/audio/after.wav, clip_info.json

Picks the shortest target call that went FATAL -> not FATAL (best demo), else the shortest
target call. Bot and buyer get different voices.

Usage: .venv/bin/python pipeline/voice.py [--call CALL_ID]
Env: TTS_MODEL (default bulbul:v3), BOT_VOICE (tanya), BUYER_VOICE (rahul)
"""
import argparse
import base64
import io
import os
import re
import wave

from common import OUTPUTS, _llm_client, now_iso, read_json, setup_logging, timeline_update, write_json

TTS_MODEL = os.environ.get("TTS_MODEL", "bulbul:v3")
BOT_VOICE = os.environ.get("BOT_VOICE", "tanya")
BUYER_VOICE = os.environ.get("BUYER_VOICE", "rahul")
AUDIO = OUTPUTS / "audio"
log = setup_logging()


def chunks(text, n=450):
    parts, cur = [], ""
    for s in re.split(r"(?<=[.!?।])\s+", text.strip()):
        if len(cur) + len(s) + 1 > n and cur:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    return [p for p in parts + [cur] if p] or [text[:n]]


def tts_wav(text, speaker):
    client, limiter = _llm_client()
    limiter.acquire()
    lang = "hi-IN" if re.search(r"[ऀ-ॿ]", text) else "en-IN"
    r = client.text_to_speech.convert(text=text, language_code=lang, model=TTS_MODEL, speaker=speaker)
    return base64.b64decode(r.audios[0])


def make(turns, path):
    frames, params = [], None
    for role, text in turns:
        for ch in chunks(text):
            w = wave.open(io.BytesIO(tts_wav(ch, BOT_VOICE if role == "BOT" else BUYER_VOICE)))
            params = params or w.getparams()
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (params.nchannels, params.sampwidth, params.framerate):
                log.warning("skipped a clip with a different audio format")
                continue
            frames.append(w.readframes(w.getnframes()))
        frames.append(b"\x00" * int(params.framerate * params.sampwidth * params.nchannels * 0.4))  # 0.4 s pause
    with wave.open(str(path), "wb") as out:
        out.setparams(params)
        for f in frames:
            out.writeframes(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--call", help="voice this call instead of picking one")
    args = ap.parse_args()
    res = (read_json(OUTPUTS / "test_results.json") or {}).get("results")
    if not res:
        raise SystemExit("Run pipeline/test_fix.py first.")
    tg = [r for r in res if r["group"] == "target"]
    if args.call:
        pick = next(r for r in res if r["call_id"] == args.call)
    else:
        good = [r for r in tg if r["before"]["grade"] == "FATAL" and r["after"]["grade"] != "FATAL"]
        if not good:
            log.warning("no call went FATAL -> not FATAL; using the shortest target call")
        pick = min(good or tg, key=lambda r: len(r["before"]["turns"]) + len(r["after"]["turns"]))
    AUDIO.mkdir(parents=True, exist_ok=True)
    make(pick["before"]["turns"], AUDIO / "before.wav")
    make(pick["after"]["turns"], AUDIO / "after.wav")
    write_json(AUDIO / "clip_info.json", {"call_id": pick["call_id"], "created_at": now_iso(), "tts_model": TTS_MODEL,
                                          "bot_voice": BOT_VOICE, "buyer_voice": BUYER_VOICE,
                                          "before_grade": pick["before"]["grade"], "after_grade": pick["after"]["grade"]})
    timeline_update("voice", finished_at=now_iso(), status="complete", call=pick["call_id"])
    log.info("saved outputs/audio/before.wav and after.wav for %s", pick["call_id"])


if __name__ == "__main__":
    main()
