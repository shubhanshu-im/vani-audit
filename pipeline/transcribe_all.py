"""Transcribe every recording with Sarvam batch STT into data/transcripts/raw/{call_id}.json.

Resumable: recordings that already have a raw JSON are skipped. Runs up to --max-jobs
batch jobs at once. All Sarvam API calls (create, upload, start, status polls,
downloads) share one rate limiter, since the batch STT limit counts every request
(Starter plan: 20 req/min per account).

Usage:
    .venv/bin/python pipeline/transcribe_all.py                 # everything pending
    .venv/bin/python pipeline/transcribe_all.py --limit 5       # first 5 pending
    .venv/bin/python pipeline/transcribe_all.py --files 470_90685058_U6YFJ1UG5 ...
"""
import argparse
import collections
import json
import os
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from common import (RAW_DIR, RECORDINGS, ROOT, TIMELINE, TRANSCRIPT_ERRORS, RateLimiter, call_id_of, now_iso,
                    setup_logging, timeline_update, update_json)

STT_SETTINGS = dict(model="saaras:v4", mode="codemix", language_code="unknown",
                    with_diarization=True, num_speakers=2)
TMP_DIR = ROOT / "data" / "transcripts" / "_tmp"
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

log = setup_logging()


class Transcriber:
    def __init__(self, args):
        load_dotenv(ROOT / ".env")
        self.client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
        self.limiter = RateLimiter(args.rpm)
        self.args = args

    def api(self, fn, *a, cost=1, what="", **kw):
        """Rate-limited call with exponential backoff on throttling, 5xx and network errors."""
        delay = 10
        for attempt in range(1, 6):
            self.limiter.acquire(cost)
            try:
                return fn(*a, **kw)
            except ApiError as e:
                if e.status_code not in RETRYABLE_STATUS or attempt == 5:
                    raise
                log.warning("%s: HTTP %s, retry %d in %ds", what, e.status_code, attempt, delay)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt == 5:
                    raise
                log.warning("%s: %s, retry %d in %ds", what, type(e).__name__, attempt, delay)
            time.sleep(delay)
            delay = min(delay * 2, 120)

    def run_batch(self, paths):
        """Run one batch job. Returns (succeeded call_ids, {call_id: error}, job_id)."""
        ids = {p.name: call_id_of(p) for p in paths}
        job = self.api(self.client.speech_to_text_job.create_job, what="create_job", **STT_SETTINGS)
        jid = job.job_id
        log.info("job %s: uploading %d files", jid, len(paths))
        self.api(job.upload_files, [str(p) for p in paths], timeout=120, what=f"{jid} upload")
        self.api(job.start, what=f"{jid} start")

        deadline = time.monotonic() + self.args.job_timeout
        while True:
            time.sleep(self.args.poll)
            st = self.api(job.get_status, what=f"{jid} status")
            state = (st.job_state or "").lower()
            if state in {"completed", "failed"}:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"job {jid} still {st.job_state} after {self.args.job_timeout}s")

        failed, ok_inputs = {}, set()
        for d in st.job_details or []:
            if not d.inputs:
                continue
            name = d.inputs[0].file_name
            if d.state == "Success" and d.outputs:
                ok_inputs.add(name)
            else:
                failed[ids.get(name, name)] = f"{d.state}: {d.error_message or 'no error message'}"
        if state == "failed" and not ok_inputs and not failed:
            failed = {cid: f"job failed: {st.error_message}" for cid in ids.values()}

        succeeded = []
        if ok_inputs:
            tmp = TMP_DIR / jid
            # download_outputs makes 2 API calls (status + download links)
            self.api(job.download_outputs, str(tmp), cost=2, what=f"{jid} download")
            for name in ok_inputs:
                src, cid = tmp / f"{name}.json", ids[name]
                try:
                    json.loads(src.read_text(encoding="utf-8"))  # validate before keeping
                    os.replace(src, RAW_DIR / f"{cid}.json")
                    succeeded.append(cid)
                except (OSError, ValueError) as e:
                    failed[cid] = f"bad or missing output: {e}"
            shutil.rmtree(tmp, ignore_errors=True)
        for name, cid in ids.items():
            if cid not in succeeded and cid not in failed:
                failed[cid] = "not reported in job results"
        return succeeded, failed, jid


def record_errors(failed, succeeded, paths_by_id, job_id):
    def apply(errs):
        for cid in succeeded:
            errs.pop(cid, None)
        for cid, msg in failed.items():
            e = errs.setdefault(cid, {"file": str(paths_by_id[cid].relative_to(ROOT)), "attempts": 0})
            e.update(attempts=e["attempts"] + 1, last_error=msg, job_id=job_id, updated_at=now_iso())
    update_json(TRANSCRIPT_ERRORS, apply)


def select_files(args):
    recs = sorted(RECORDINGS.glob("*.mp3"))
    if args.files:
        wanted = {f.split(".")[0] for f in args.files}
        recs = [p for p in recs if call_id_of(p) in wanted]
        missing = wanted - {call_id_of(p) for p in recs}
        if missing:
            raise SystemExit(f"not found in {RECORDINGS}: {sorted(missing)}")
    pending = [p for p in recs if not (RAW_DIR / f"{call_id_of(p)}.json").exists()]
    return len(recs), (pending[: args.limit] if args.limit else pending)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="call_ids or filenames to transcribe (default: all)")
    ap.add_argument("--limit", type=int, help="only the first N pending files")
    ap.add_argument("--batch-size", type=int, default=20, help="files per job (Sarvam max 20)")
    ap.add_argument("--max-jobs", type=int, default=3, help="jobs running at once")
    ap.add_argument("--rpm", type=int, default=15, help="Sarvam STT API requests/min across all jobs")
    ap.add_argument("--poll", type=int, default=20, help="seconds between job status polls")
    ap.add_argument("--job-timeout", type=int, default=1800, help="seconds before a job is abandoned")
    ap.add_argument("--max-attempts", type=int, default=3, help="tries per file within this run")
    args = ap.parse_args()
    args.batch_size = max(1, min(args.batch_size, 20))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    total, pending = select_files(args)
    all_recs = len(list(RECORDINGS.glob("*.mp3")))
    log.info("%d recordings selected, %d already transcribed, %d to do",
             total, total - len(pending), len(pending))
    if not pending:
        return

    run = {"started_at": now_iso(), "files": len(pending)}
    def start(tl):
        st = tl.setdefault("transcribe", {})
        st.setdefault("started_at", run["started_at"])  # first run ever starts the stage clock
        st.update(calls_total=all_recs, last_run_started_at=run["started_at"], status="running")
    update_json(TIMELINE, start)

    tr = Transcriber(args)
    by_id = {call_id_of(p): p for p in pending}
    attempts = collections.Counter()
    queue = list(pending)
    done = failed_final = 0
    t0 = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.max_jobs, thread_name_prefix="job") as pool:
        running = {}
        while queue or running:
            while queue and len(running) < args.max_jobs:
                batch, queue = queue[: args.batch_size], queue[args.batch_size:]
                for p in batch:
                    attempts[call_id_of(p)] += 1
                running[pool.submit(tr.run_batch, batch)] = batch
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in finished:
                batch = running.pop(fut)
                try:
                    ok, failed, jid = fut.result()
                except Exception as e:  # whole batch failed after API retries
                    ok, jid = [], None
                    failed = {call_id_of(p): f"{type(e).__name__}: {e}" for p in batch}
                    log.error("batch of %d failed: %s", len(batch), e)
                record_errors(failed, ok, by_id, jid)
                done += len(ok)
                for cid, msg in failed.items():
                    if attempts[cid] < args.max_attempts:
                        log.warning("%s failed (attempt %d), requeued: %s", cid, attempts[cid], msg)
                        queue.append(by_id[cid])
                    else:
                        failed_final += 1
                        log.error("%s failed permanently after %d attempts: %s", cid, attempts[cid], msg)
                elapsed = time.monotonic() - t0
                remaining = len(pending) - done - failed_final
                eta = elapsed / done * remaining if done else 0
                log.info("job %s: +%d ok, %d failed | progress %d/%d done, %d failed, %d left | "
                         "elapsed %.0fs, ETA %.0fs", jid, len(ok), len(failed), done, len(pending),
                         failed_final, remaining, elapsed, eta)
                have = len(list(RAW_DIR.glob("*.json")))
                timeline_update("transcribe", calls_done=have, calls_failed=failed_final,
                                updated_at=now_iso())

    have = len(list(RAW_DIR.glob("*.json")))
    run.update(finished_at=now_iso(), ok=done, failed=failed_final)
    complete = have >= all_recs
    def finish(tl):
        st = tl.setdefault("transcribe", {})
        st.setdefault("runs", []).append(run)
        st.update(calls_done=have, status="complete" if complete else "partial", updated_at=now_iso())
        if complete:
            st["finished_at"] = run["finished_at"]
    update_json(TIMELINE, finish)
    log.info("run finished: %d ok, %d failed; %d/%d recordings now transcribed",
             done, failed_final, have, all_recs)
    if failed_final:
        log.info("failures logged in %s", TRANSCRIPT_ERRORS.relative_to(ROOT))


if __name__ == "__main__":
    main()
