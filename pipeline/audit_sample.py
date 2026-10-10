"""Build the human-audited calibration set from IndiaMART's audit sheet ("Audit Source of truth.csv").

    data/audit/audit_source_of_truth.csv  ->  data/audit/recordings/*.mp3 + data/audit/labels.csv

The sheet holds ~4,900 VANI calls graded by IndiaMART auditors with the same rubric we use
(Pass / Non-Fatal / Fatal / NA per parameter, remarks, and Call / BL / Overall Quality).
None of them are among our 713 recordings, but the SQUADSTACK recordings (same vendor and
storage as ours) download publicly, so we can run our pipeline on them and measure
agreement with real auditors.

Selection: SQUADSTACK INBOUND calls with a recording, stratified by Overall Quality
(Fatal / Non-Fatal / Pass, equal thirds by default), fixed seed; within each stratum the
calls alternate tune / test. Tune the grader on "tune" only; report agreement on "test".

Run with VANI_DATASET=audit for every pipeline step on these calls, e.g.
    .venv/bin/python pipeline/audit_sample.py --n 160
    VANI_DATASET=audit .venv/bin/python pipeline/transcribe_all.py
    VANI_DATASET=audit .venv/bin/python pipeline/normalise.py && ... signals.py && ... grade.py
    VANI_DATASET=audit .venv/bin/python pipeline/agreement.py
"""
import argparse
import csv
import random
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from common import ROOT, setup_logging

AUDIT = ROOT / "data" / "audit"
SHEET = AUDIT / "audit_source_of_truth.csv"
LABELS = AUDIT / "labels.csv"

# audit-sheet column  ->  our rubric parameter (docs/quality_matrix.md)
PARAM_COLUMNS = {
    "Outcome Accuracy \n(Intent)": "Outcome Accuracy",
    "Quantity Accuracy (Numeric)": "Quantity Accuracy (Numeric)",
    "Quantity Unit Accuracy ": "Quantity Unit Accuracy",
    "Quantity Probing Error": "Quantity Probing Error",
    "Quantity Missed Opportunity (Capture) ": "Qty Missed Opportunity",
    "Spec1 \nAccuracy ": "Spec1 Accuracy",
    "Spec2 \nAccuracy ": "Spec2 Accuracy",
    "Spec Missed Opportunity ": "Spec Missed Opportunity",
    "Additional Details Accuracy": "Additional_details",
    "Looping \nBehavior ": "Looping Behavior",
    "WER/STT": "WER",
    "Dead air": "Dead air",
    "Pronunciation": "Additional Observation",
    "Product Change/ No Product Accuracy ": "Product Change / No Product Accuracy",
    "MCAT/ Search errors": "MCAT / Search errors",
    " Category ISQ's Error": "Category ISQ's Error",
    "OnCall AstBuy Error\n(Transferred)": "OnCall AstBuy Error",
}
REMARK_OF = {  # verdict column -> its remarks column
    "Outcome Accuracy \n(Intent)": "Outcome Accuracy Remarks",
    "Quantity Accuracy (Numeric)": "Quantity Accuracy (Numeric)\n Remarks",
    "Quantity Unit Accuracy ": "Quantity Unit Accuracy Remarks",
    "Quantity Probing Error": "Quantity Probing Error Remarks",
    "Quantity Missed Opportunity (Capture) ": "Quantity Missed Opportunity Remarks",
    "Spec1 \nAccuracy ": "Spec1 \nAccuracy Remarks",
    "Spec2 \nAccuracy ": "Spec2 \nAccuracy Remarks",
    "Spec Missed Opportunity ": "Spec Missed Opportunity Remarks",
    "Additional Details Accuracy": "Additional_details remarks",
    "Looping \nBehavior ": "Looping Behavior Remarks",
    "WER/STT": "WER/STT Remarks",
    "Dead air": "Dead air remarks",
    "Pronunciation": "Pronunciation Remarks",
    "Product Change/ No Product Accuracy ": "Product Change/ No Product Accuracy Reamarks",
    "MCAT/ Search errors": "MCAT/ Search errors Remarks",
    " Category ISQ's Error": " Category ISQ's Error Remarks",
    "OnCall AstBuy Error\n(Transferred)": "OnCall AstBuy Remarks",
}
log = setup_logging()


def verdict(v):
    """Normalise an audit cell to PASS / NON_FATAL / FATAL / NA ('Fatal- Product Change Case' -> FATAL)."""
    v = (v or "").strip().lower()
    if v.startswith("fatal"):
        return "FATAL"
    if v.startswith("non"):
        return "NON_FATAL"
    if v.startswith("pass"):
        return "PASS"
    return "NA"


def load_sheet():
    csv.field_size_limit(10 ** 9)
    with SHEET.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=160, help="calls to sample (split equally over Fatal / Non-Fatal / Pass)")
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--sheet", type=Path, help="path to 'Audit Source of truth.csv' (copied into data/audit/)")
    args = ap.parse_args()

    AUDIT.mkdir(parents=True, exist_ok=True)
    if args.sheet:
        shutil.copy(args.sheet, SHEET)
    if not SHEET.exists():
        raise SystemExit(f"put the audit sheet at {SHEET} or pass --sheet")
    rows = load_sheet()
    pool = []
    for r in rows:
        url = (r.get("Call Recording") or "").strip()
        m = re.search(r"squadiq-call-recs\.s3\.amazonaws\.com/([A-Z0-9]+)\.mp3", url)
        if m and r.get("Vendor ", "").strip() == "SQUADSTACK" and r.get("fk_gl_module_id") == "INBOUND":
            r["_code"], r["_url"] = m.group(1), url.replace("http://", "https://")
            r["_overall"] = verdict(r["Overall Quality"])
            pool.append(r)
    seen, uniq = set(), []
    for r in pool:  # a few calls were audited twice: keep the first
        if r["_code"] not in seen:
            seen.add(r["_code"])
            uniq.append(r)
    rng = random.Random(args.seed)
    picked = []
    strata = ["FATAL", "NON_FATAL", "PASS"]
    for i, s in enumerate(strata):
        group = sorted((r for r in uniq if r["_overall"] == s), key=lambda r: r["_code"])
        k = args.n // 3 + (1 if i < args.n % 3 else 0)
        chosen = rng.sample(group, min(k, len(group)))
        for j, r in enumerate(chosen):
            r["_split"] = "tune" if j % 2 == 0 else "test"
        picked += chosen
        log.info("%s: %d available, picked %d", s, len(group), len(chosen))

    rec_dir = AUDIT / "recordings"
    rec_dir.mkdir(exist_ok=True)

    def fetch(r):
        cid = f"audit_{(r.get('Offer ID') or 'x').strip()}_{r['_code']}"
        r["_cid"] = cid
        out = rec_dir / f"{cid}.mp3"
        if out.exists() and out.stat().st_size > 0:
            return True
        try:
            resp = httpx.get(r["_url"], timeout=60, follow_redirects=True)
            if resp.status_code == 200 and resp.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or \
                    resp.headers.get("content-type", "").startswith("audio"):
                out.write_bytes(resp.content)
                return True
            log.warning("%s: HTTP %s", cid, resp.status_code)
        except httpx.HTTPError as e:
            log.warning("%s: %s", cid, e)
        return False

    with ThreadPoolExecutor(max_workers=8) as ex:
        ok = list(ex.map(fetch, picked))
    kept = [r for r, good in zip(picked, ok) if good]
    log.info("downloaded %d/%d recordings into %s", len(kept), len(picked), rec_dir.relative_to(ROOT))

    fields = ["call_id", "split", "overall_quality", "call_quality", "bl_quality", "auditor", "audit_month",
              "recording_url"] + [f"param:{p}" for p in PARAM_COLUMNS.values()] + [f"remark:{p}" for p in PARAM_COLUMNS.values()]
    with LABELS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(kept, key=lambda r: r["_cid"]):
            row = {"call_id": r["_cid"], "split": r["_split"], "overall_quality": r["_overall"],
                   "call_quality": verdict(r["Call Quality"]), "bl_quality": verdict(r["BL Quality"]),
                   "auditor": r.get("Audited By (Auditor)", ""), "audit_month": r.get("Audit Month", ""),
                   "recording_url": r["_url"]}
            for col, param in PARAM_COLUMNS.items():
                row[f"param:{param}"] = verdict(r.get(col))
                row[f"remark:{param}"] = (r.get(REMARK_OF[col]) or "").strip()
            w.writerow(row)
    log.info("wrote %s (%d calls: %s)", LABELS.relative_to(ROOT), len(kept),
             {s: sum(r["_overall"] == s for r in kept) for s in strata})


if __name__ == "__main__":
    main()
