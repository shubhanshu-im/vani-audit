"""Agreement between the auto-grader and human labels.

Two label sources, picked by VANI_DATASET:
- main  (default): our hand labels, data/labels/human_labels.csv (FATAL / NON_FATAL per call).
- audit: IndiaMART auditors' verdicts, data/audit/labels.csv (from pipeline/audit_sample.py):
         Overall Quality PASS / NON_FATAL / FATAL plus a verdict per rubric parameter.

Reports, overall and per split (headline = "test"; the grader is tuned on "tune" only):
- binary FATAL vs not FATAL: agreement %, Cohen's kappa, confusion matrix
- (audit) 3-class quality agreement, and a population-weighted agreement: the audit sample is
  stratified (equal thirds) while real traffic is about 69% Pass / 19% Non-Fatal / 12% Fatal,
  so each call is weighted by population share / sample share of its human class
- (audit) per-parameter agreement on "is there an issue" and on "is it FATAL"
- every disagreement, so the team can explain it

    outputs[/<dataset>]/agreement.json

Usage: [VANI_DATASET=audit] .venv/bin/python pipeline/agreement.py
"""
import csv
from collections import Counter

from common import DATASET, OUTPUTS, ROOT, load_grades, now_iso, rubric_params, setup_logging, timeline_update, write_json

HAND_LABELS = ROOT / "data" / "labels" / "human_labels.csv"
AUDIT_LABELS = ROOT / "data" / "audit" / "labels.csv"
# Inbound SQUADSTACK calls in the audit sheet (n = 1,877): Overall Quality distribution
POPULATION = {"PASS": 0.689, "NON_FATAL": 0.186, "FATAL": 0.125}
log = setup_logging()


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    labels = sorted({x for p in pairs for x in p})
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == l for a, _ in pairs) / n) * (sum(b == l for _, b in pairs) / n) for l in labels)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def stats(pairs, weights=None):
    """pairs: [(human, auto)]. Returns agreement %, kappa, confusion, optional weighted agreement."""
    n = len(pairs)
    if not n:
        return {"n": 0, "agreement_pct": None, "kappa": None}
    out = {"n": n, "agreement_pct": round(100 * sum(h == a for h, a in pairs) / n, 1), "kappa": kappa(pairs),
           "confusion": {f"human_{h}__auto_{a}": c for (h, a), c in sorted(Counter(pairs).items())}}
    if weights:
        w = sum(weights)
        out["weighted_agreement_pct"] = round(100 * sum(wt for (h, a), wt in zip(pairs, weights) if h == a) / w, 1)
    return out


def binary(x):
    return "FATAL" if x == "FATAL" else "NOT_FATAL"


def hand_labels(grades):
    rows = []
    if HAND_LABELS.exists():
        with HAND_LABELS.open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if (r.get("grade") or "").strip()]
    pairs, dis = [], []
    for r in rows:
        human = r["grade"].strip().upper().replace("-", "_").replace(" ", "_")
        g = grades.get(r["call_id"])
        if human not in ("FATAL", "NON_FATAL") or not g or g["grade"] not in ("FATAL", "NON_FATAL"):
            continue
        pairs.append((r.get("split", ""), human, g["grade"]))
        if human != g["grade"]:
            dis.append({"call_id": r["call_id"], "split": r.get("split"), "human": human, "auto": g["grade"],
                        "human_reason": r.get("fatal_reason", ""), "auto_reason": g.get("grade_reason", "")})
    by_split = {s: stats([(h, a) for sp, h, a in pairs if sp == s]) for s in ("tune", "test")}
    overall = stats([(h, a) for _, h, a in pairs])
    head = "test" if by_split["test"]["n"] else "all"
    hs = by_split["test"] if head == "test" else overall
    return {"source": "hand labels", "agreement_pct": hs["agreement_pct"], "kappa": hs["kappa"], "headline_split": head,
            "n_labelled": len(rows), "overall": overall, "by_split": by_split, "disagreements": dis}


def audit_labels(grades):
    with AUDIT_LABELS.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    sample_share = Counter(r["overall_quality"] for r in rows)
    weight = {k: POPULATION[k] / (sample_share[k] / len(rows)) for k in sample_share if k in POPULATION}
    params = rubric_params()
    res, dis, skipped = {}, [], []
    for split in ("tune", "test", "all"):
        sel = [r for r in rows if split == "all" or r["split"] == split]
        b, q, cq, w, per = [], [], [], [], {p: {"issue": [], "fatal": []} for p in params}
        for r in sel:
            g = grades.get(r["call_id"])
            if not g:
                if split == "all":
                    skipped.append(r["call_id"])
                continue
            human = r["overall_quality"]
            auto_q = g.get("quality") or ("NA" if g["grade"] == "NO_CONVERSATION" else g["grade"])
            b.append((binary(human), binary(auto_q)))
            q.append((human, auto_q if auto_q in POPULATION else "PASS"))
            cq.append((binary(r["call_quality"]), binary(auto_q)))
            w.append(weight.get(human, 1.0))
            verdicts = {p["param"]: p.get("verdict") for p in g.get("parameters", [])}
            for p in params:
                hv, av = r.get(f"param:{p}", "NA"), verdicts.get(p) or "NA"
                per[p]["issue"].append((hv in ("FATAL", "NON_FATAL"), av in ("FATAL", "NON_FATAL")))
                per[p]["fatal"].append((hv == "FATAL", av == "FATAL"))
            if split == "all" and binary(human) != binary(auto_q):
                dis.append({"call_id": r["call_id"], "split": r["split"], "human": human, "auto": auto_q,
                            "human_fatal_params": [p for p in params if r.get(f"param:{p}") == "FATAL"],
                            "human_remarks": {p: r[f"remark:{p}"] for p in params if r.get(f"param:{p}") == "FATAL"
                                              and r.get(f"remark:{p}")},
                            "auto_fatal_params": g.get("fatal_params", []), "auto_reason": g.get("grade_reason", "")})
        res[split] = {"fatal_vs_not": stats(b, w), "quality_3class": stats(q, w),
                      "fatal_vs_not_call_quality": stats(cq),
                      "per_param": {p: {"issue_agreement_pct": stats(v["issue"])["agreement_pct"],
                                        "human_issues": sum(h for h, _ in v["issue"]), "auto_issues": sum(a for _, a in v["issue"]),
                                        "human_fatal": sum(h for h, _ in v["fatal"]), "auto_fatal": sum(a for _, a in v["fatal"]),
                                        "both_fatal": sum(h and a for h, a in v["fatal"])}
                                    for p, v in per.items() if v["issue"]}}
    head = res["test"] if res["test"]["fatal_vs_not"]["n"] else res["all"]
    return {"source": "IndiaMART audit sheet (human auditors)", "headline_split": "test" if res["test"]["fatal_vs_not"]["n"] else "all",
            "agreement_pct": head["fatal_vs_not"]["agreement_pct"], "kappa": head["fatal_vs_not"]["kappa"],
            "weighted_agreement_pct": head["fatal_vs_not"].get("weighted_agreement_pct"),
            "quality_3class_agreement_pct": head["quality_3class"]["agreement_pct"],
            "n_labelled": len(rows), "population_weights": weight, "by_split": res, "disagreements": dis, "skipped": skipped}


def main():
    grades = load_grades()
    out = audit_labels(grades) if DATASET == "audit" else hand_labels(grades)
    out["created_at"] = now_iso()
    out["dataset"] = DATASET
    if not out["n_labelled"]:
        out["note"] = "No human labels yet: fill data/labels/human_labels.csv (see data/labels/LABELLING_GUIDE.md)."
    write_json(OUTPUTS / "agreement.json", out)
    timeline_update("agreement", finished_at=now_iso(), status="complete" if out["n_labelled"] else "no_labels",
                    labelled=out["n_labelled"])
    log.info("[%s] %s: FATAL-vs-not agreement %s%% (kappa %s, %s split)%s | %d disagreements",
             DATASET, out["source"], out["agreement_pct"], out["kappa"], out["headline_split"],
             f" | population-weighted {out.get('weighted_agreement_pct')}% | 3-class {out.get('quality_3class_agreement_pct')}%"
             if DATASET == "audit" else "", len(out["disagreements"]))


if __name__ == "__main__":
    main()
