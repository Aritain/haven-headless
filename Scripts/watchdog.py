"""
watchdog.py - detects and cleans up orphaned or stuck Hurricane
headless-client processes.

Runs as a background thread inside the same app process (no cron needed -
useful in a container, which often doesn't run a cron daemon at all).

Two kinds of process get reaped:

  * Orphaned: parent process has died and it has been reparented to PID 1
    by the kernel - i.e. whatever spawned it (our own scout_lib.run_scout
    call) is no longer around to manage or clean it up. Killed regardless
    of age.

  * Stuck: still parented by our app, but running far longer than any real
    scout run should take (e.g. hung waiting on a display, or stalled
    mid-login). Killed once older than MAX_RUN_SECONDS.

Termination is graceful first (SIGTERM), escalating to SIGKILL if the
process is still alive after a short grace period.
"""

import threading
import time

import psutil

import db

POLL_SECONDS = 30
MAX_RUN_SECONDS = 300   # a full multi-road run should finish well within this
GRACE_SECONDS = 5       # after SIGTERM, wait this long before SIGKILL
MATCH_STRING = "haven.HeadlessClient"

_stop = threading.Event()


def _matches(proc_cmdline):
    return any(MATCH_STRING in part for part in proc_cmdline)


def _is_orphaned(proc):
    try:
        return proc.ppid() == 1
    except psutil.NoSuchProcess:
        return False


def _terminate(proc):
    """SIGTERM the process, escalating to SIGKILL if it doesn't exit within
    the grace period. Kills child processes too, since xvfb-run spawns java
    (and its temporary Xvfb) as children."""
    try:
        procs = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        procs = []
    procs.append(proc)

    for p in procs:
        try:
            p.terminate()  # SIGTERM
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    gone, alive = psutil.wait_procs(procs, timeout=GRACE_SECONDS)
    for p in alive:
        try:
            p.kill()  # SIGKILL
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def scan_once():
    """Runs one scan/cleanup pass. Returns the list of pids it terminated
    (exposed as a function, not just folded into the loop, so it can be
    called directly / tested without waiting on the poll interval)."""
    killed = []
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmdline = proc.info["cmdline"] or []
            if not _matches(cmdline):
                continue

            age = time.time() - (proc.info["create_time"] or time.time())
            # Orphan-kill disabled: every crash we could correlate had a
            # watchdog orphan_killed event within milliseconds of it,
            # including one only 3s after the process started - it was
            # killing live, still-legitimately-parented processes, not
            # actual orphans. Root cause not nailed down; disabled until it
            # is. Stuck-process reaping (>300s) stays active as the backstop
            # against runaway processes.
            orphaned = False
            stuck = age > MAX_RUN_SECONDS

            if not orphaned and not stuck:
                continue

            pid = proc.pid
            reason = "orphaned" if orphaned else "stuck/overrunning"
            try:
                _terminate(proc)
                killed.append(pid)
                msg = f"Watchdog killed {reason} process pid={pid} age={int(age)}s"
                print(msg)
                db.log_event("watchdog", "orphan_killed", msg, level="error")
            except psutil.NoSuchProcess:
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def _poll_loop():
    while not _stop.is_set():
        try:
            scan_once()
        except Exception as e:
            print(f"Watchdog error: {e}")
        _stop.wait(POLL_SECONDS)


def start():
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
