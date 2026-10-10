"""Write a masked copy of the dashboard's inputs to demo_data/, so a hosted dashboard works from git.

    outputs/grades/, data/transcripts/clean/, outputs/{root_causes,test_results,agreement}.json,
    outputs/audit/agreement.json  ->  demo_data/ (same file names and formats)

The raw recordings, raw STT output and per-call outputs stay git-ignored. This copy masks buyer
names and phone numbers (the same Masker as export_cowork.py) and leaves out all audio.
dashboard.py reads demo_data/ only when outputs/grades/ is empty, e.g. on Streamlit Community Cloud.

Usage: .venv/bin/python pipeline/export_demo_data.py
"""
import re
import shutil
from collections import Counter

from common import OUTPUTS, ROOT, load_clean, load_grades, read_json, setup_logging, write_json
from export_cowork import Masker

DEMO = ROOT / "demo_data"
AGGREGATES = ["root_causes.json", "test_results.json", "agreement.json", "audit/agreement.json"]
MAX_NAME_COUNT = 50   # the most frequent real buyer name occurs 14 times in all transcripts
log = setup_logging()


def mask_all(obj, mask, names):
    """Mask every string inside a JSON value."""
    if isinstance(obj, str):
        return mask.apply(obj, names)
    if isinstance(obj, list):
        return [mask_all(x, mask, names) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_all(v, mask, names) for k, v in obj.items()}
    return obj


def main():
    grades = load_grades()
    if not grades:
        raise SystemExit("No grades in outputs/grades/: run the pipeline first.")
    mask = Masker(True)
    cleans = {cid: load_clean(cid) for cid in grades}
    # the greeting pattern sometimes catches everyday words ("मैं", "तो", "Hello"); real names are rare
    # across 700 calls, so a candidate that is common in the transcripts is not treated as a name
    word_count = Counter(w for c in cleans.values() if c for t in c["turns"] for w in re.findall(r"[\wऀ-ॿ]+", t["text"]))
    if DEMO.exists():
        shutil.rmtree(DEMO)
    every_name = set()
    for cid, g in grades.items():
        clean = cleans[cid]
        names = {n.strip(",.?।") for n in mask.names_for(g, clean)}
        names = {n for n in names if len(n) > 1 and word_count[n] <= MAX_NAME_COUNT}
        every_name |= names
        g = mask_all(g, mask, names)
        if g.get("captured_lead"):
            g["captured_lead"]["name"] = "[NAME]" if g["captured_lead"].get("name") else g["captured_lead"].get("name")
        write_json(DEMO / "grades" / f"{cid}.json", g)
        if clean:
            clean = dict(clean, turns=[dict(t, text=mask.apply(t["text"], names)) for t in clean["turns"]])
            write_json(DEMO / "clean" / f"{cid}.json", clean)
    # aggregate files quote lines from many calls: mask every buyer name seen anywhere
    for rel in AGGREGATES:
        data = read_json(OUTPUTS / rel)
        if data is not None:
            write_json(DEMO / rel, mask_all(data, mask, every_name))
    log.info("wrote %d grades, %d transcripts and %d aggregate files to %s (names masked: %d)",
             len(grades), len(list((DEMO / "clean").glob("*.json"))),
             sum((DEMO / r).exists() for r in AGGREGATES), DEMO.relative_to(ROOT), len(every_name))


if __name__ == "__main__":
    main()
