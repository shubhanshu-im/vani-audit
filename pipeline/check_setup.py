"""Quick health check: what is ready and what is missing (from Person 2's check_setup.py).

Usage: .venv/bin/python pipeline/check_setup.py [--no-api]
"""
import argparse
import os

from common import (BOT_PROMPT_MD, CLEAN_DIR, MODEL_CHAT, MODEL_MAIN, OUTPUTS, RAW_DIR, RECORDINGS, ROOT, SIGNALS_DIR,
                    load_grades, llm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-api", action="store_true", help="skip the live LLM and TTS calls")
    a = ap.parse_args()
    ok = lambda b: "OK      " if b else "MISSING "
    n_rec = len(list(RECORDINGS.glob("*.mp3")))
    count = lambda d: len(list(d.glob("*.json"))) if d.exists() else 0
    grades = load_grades()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    print(ok(os.environ.get("SARVAM_API_KEY")), "SARVAM_API_KEY in .env")
    print(ok(n_rec), f"recordings: {n_rec}")
    for name, d in (("raw transcripts", RAW_DIR), ("clean transcripts", CLEAN_DIR), ("signals", SIGNALS_DIR)):
        print(ok(count(d) >= n_rec > 0), f"{name}: {count(d)}/{n_rec}")
    print(ok(len(grades) >= n_rec > 0), f"grades: {len(grades)}/{n_rec}")
    print(ok(BOT_PROMPT_MD.exists()), "bot prompt template docs/vani_buyer_prompt.md")
    print(ok((ROOT / "prompts" / "grading_spec.md").exists()), "grading spec prompts/grading_spec.md")
    labels = ROOT / "data" / "labels" / "human_labels.csv"
    print(ok(labels.exists()), "label set data/labels/human_labels.csv")
    for f in ("agreement.json", "root_causes.json", "ranking.json", "fixes.json", "test_results.json", "audio/after.wav"):
        print(ok((OUTPUTS / f).exists()), f"outputs/{f}")
    if a.no_api:
        return
    for model in (MODEL_MAIN, MODEL_CHAT):
        try:
            print(ok("ok" in llm("Reply with the word ok.", "Say ok.", model=model, temperature=0, cache=False).lower()),
                  f"LLM {model}")
        except Exception as e:
            print("FAILED  ", f"LLM {model}: {e}")
    try:
        from voice import TTS_MODEL, BOT_VOICE, tts_wav
        print(ok(len(tts_wav("नमस्ते", BOT_VOICE)) > 100), f"Bulbul TTS ({TTS_MODEL}, voice {BOT_VOICE})")
    except Exception as e:
        print("FAILED  ", f"Bulbul TTS: {e}")


if __name__ == "__main__":
    main()
