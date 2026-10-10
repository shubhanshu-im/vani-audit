"""Run the whole audit -> issue -> fix -> test loop in order (from Person 2's run_person2.py).

Every step is resumable, so re-running skips work already done.

    1 transcribe   2 normalise    3 signals     4 grade      5 agreement
    6 root_causes  7 rank         8 prompt_fix  9 test_fix  10 voice      11 summary

Usage:
    .venv/bin/python pipeline/run_all.py                  # everything
    .venv/bin/python pipeline/run_all.py --from 6         # from root causes onwards
    .venv/bin/python pipeline/run_all.py --to 4           # audit only
    .venv/bin/python pipeline/run_all.py --from 9 --cause C2 --skip-voice
Then: .venv/bin/streamlit run pipeline/dashboard.py
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["transcribe_all", "normalise", "signals", "grade", "agreement",
         "root_causes", "rank", "prompt_fix", "test_fix", "voice", "summary"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", dest="end", type=int, default=len(STEPS))
    ap.add_argument("--skip-voice", action="store_true")
    ap.add_argument("--cause", help="cause id for test_fix (default: first fixed cause)")
    ap.add_argument("--combined", action="store_true", help="test_fix with all fixes together")
    a = ap.parse_args()
    extra = {"test_fix": ([a.cause] if a.cause else []) + (["--combined"] if a.combined else [])}
    t0 = time.time()
    for n, step in enumerate(STEPS, 1):
        if not a.start <= n <= a.end or (step == "voice" and a.skip_voice):
            continue
        print(f"\n=== Step {n}: {step}", flush=True)
        t = time.time()
        if subprocess.run([sys.executable, str(HERE / f"{step}.py")] + extra.get(step, [])).returncode:
            sys.exit(f"\nStopped at {step}. Fix the problem, then continue with: "
                     f".venv/bin/python pipeline/run_all.py --from {n}")
        print(f"--- {step} done in {time.time() - t:.0f}s", flush=True)
    print(f"\nAll done in {(time.time() - t0) / 60:.1f} min. Dashboard: .venv/bin/streamlit run pipeline/dashboard.py")


if __name__ == "__main__":
    main()
