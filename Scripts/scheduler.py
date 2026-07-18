import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import db
import scout_lib
import crypto_util
from notifier import notify_found

POLL_SECONDS = 15
MAX_PARALLEL = 4  # different accounts can run at the same time, up to this many

_executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
_account_locks = {}
_account_locks_guard = threading.Lock()
_stop = threading.Event()


def _lock_for_account(account_id):
    with _account_locks_guard:
        if account_id not in _account_locks:
            _account_locks[account_id] = threading.Lock()
        return _account_locks[account_id]


def _is_due(job):
    if not job["last_run_at"]:
        return True
    last = datetime.fromisoformat(job["last_run_at"])
    return datetime.now(timezone.utc) >= last + timedelta(minutes=job["interval_minutes"])


def _run_job(job_id):
    job = db.get_job(job_id)
    if job is None:
        return
    lock = _lock_for_account(job["account_id"])
    if not lock.acquire(blocking=False):
        # another character on this account is currently running; try again next poll
        db.set_job_running(job_id, False)
        return
    try:
        bindir = db.get_setting("bindir")
        server = db.get_setting("server", "game.havenandhearth.com")
        if not bindir:
            msg = "ERROR: bindir not set"
            db.record_job_result(job_id, datetime.now(timezone.utc).isoformat(), msg)
            db.log_event("scheduler", "job_error",
                         f"{job['character_name']} ({job['account_label']}): bindir not set in Settings",
                         level="error")
            return
        account = db.get_account(job["account_id"])
        password = crypto_util.decrypt(account["password_encrypted"]) if account else None
        if not password:
            msg = "ERROR: No password saved for this account. Add one on the Accounts page."
            db.record_job_result(job_id, datetime.now(timezone.utc).isoformat(), msg)
            db.log_event("scheduler", "job_error",
                         f"{job['character_name']} ({job['account_label']}): no password saved for account",
                         level="error")
            return
        roads = db.get_job_roads(job_id)
        if not roads:
            msg = "ERROR: No roads configured for this job."
            db.record_job_result(job_id, datetime.now(timezone.utc).isoformat(), msg)
            db.log_event("scheduler", "job_error",
                         f"{job['character_name']} ({job['account_label']}): no roads configured",
                         level="error")
            return
        outcome = scout_lib.run_scout(
            bindir, job["account_username"], job["character_name"],
            roads, job["gob_name"], server, password=password,
        )
        db.record_job_result(job_id, datetime.now(timezone.utc).isoformat(), outcome["result"])
        if outcome["found_roads"]:
            for road in outcome["found_roads"]:
                notify_found(job["account_label"], job["character_name"], road, job["gob_name"])
            roads_str = ", ".join(outcome["found_roads"])
            db.log_event("scheduler", "job_found",
                         f"{job['character_name']} ({job['account_label']}) found {job['gob_name']} "
                         f"on: {roads_str}", level="success")
        elif outcome["result"] == "NOTFOUND":
            db.log_event("scheduler", "job_notfound",
                         f"{job['character_name']} ({job['account_label']}) checked "
                         f"{', '.join(roads)} for {job['gob_name']} — not found", level="info")
        else:
            db.log_event("scheduler", "job_error",
                         f"{job['character_name']} ({job['account_label']}): {outcome['result']}",
                         level="error")
    finally:
        lock.release()


def _poll_loop():
    while not _stop.is_set():
        try:
            jobs = db.due_jobs(None)
            for job in jobs:
                if _is_due(job):
                    db.set_job_running(job["id"], True)
                    _executor.submit(_run_job, job["id"])
        except Exception as e:
            print(f"Scheduler poll error: {e}")
        _stop.wait(POLL_SECONDS)


def start():
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()


def run_now(job_id):
    """Trigger a job immediately, outside its normal interval."""
    db.set_job_running(job_id, True)
    _executor.submit(_run_job, job_id)
