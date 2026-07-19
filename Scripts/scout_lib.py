"""
scout_lib.py - core logic for driving one headless-client scouting run.

Shared by scout.py (CLI) and app.py (web dashboard) so there's a single
tested implementation instead of two copies drifting apart.
"""

import queue
import subprocess
import threading
import time

import psutil

JAVA_ARGS = [
    # No -Xmx/-XX:+UseSerialGC here on purpose: a capped heap + the
    # stop-the-world serial collector meant frequent, longer GC pauses that
    # froze every JVM thread - including the one servicing the X11 socket -
    # long enough to trip JogAmp's connection timeout and crash the client
    # with a fatal "Nativewindow X11 IOError". Memory is bounded by the
    # other layers instead: scheduler.MAX_PARALLEL, docker-compose's
    # mem_limit backstop, and watchdog reaping orphaned/stuck processes.
    "--add-exports=java.base/java.lang=ALL-UNNAMED",
    "--add-exports=java.desktop/sun.awt=ALL-UNNAMED",
    "--add-exports=java.desktop/sun.java2d=ALL-UNNAMED",
    "--enable-native-access=ALL-UNNAMED",
    "-cp", "*",
    "haven.HeadlessClient",
]

# HeadlessClient defaults its offscreen render target to 1920x1080 (real
# color+depth textures at that size) even though nothing ever displays it,
# which is a big chunk of why each instance's RSS runs 1GB+. The scout bot
# only reads the widget tree over stdin/stdout, never pixels, so shrink it
# with the client's own -s flag.
HEADLESS_RENDER_SIZE = "320x240"

DEFAULT_DELAYS = {
    "boot": 6.0,
    "login": 8.0,
    "approach": 4.0,
    "travelapproach": 8.0,
    "settle": 3.0,
    "short": 1.5,
    "teleport": 5.0,  # used for :hearth
}


def start_client(bindir, user, server, password=None, display=None):
    # Hurricane's headless client still initializes AWT/JOGL. Run it under a
    # temporary virtual X server so it can do so without a physical display.
    #
    # xvfb-run's own "-a" auto-picks a free display number via a check-then-act
    # loop that isn't atomic, so two instances launched close together (as
    # happens with concurrent scout jobs) can race onto the same number and
    # fail with X11 "Resource temporarily unavailable"/"Invalid argument". If
    # the caller hands us a display slot (see scheduler.py's pool), use it
    # directly instead of trusting -a to pick uniquely.
    if display is not None:
        xvfb_opts = ["--server-num", str(display)]
    else:
        xvfb_opts = ["-a"]
    cmd = ["xvfb-run", *xvfb_opts, "java", *JAVA_ARGS]
    if password is not None:
        cmd += ["-s", HEADLESS_RENDER_SIZE, "-u", user, "-w", server]
    else:
        cmd += ["-s", HEADLESS_RENDER_SIZE, "-u", user, server]
    proc = subprocess.Popen(
        cmd,
        cwd=bindir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if password is not None:
        # Sent over the private stdin pipe, never as a command-line argument
        # (which would be visible to other processes via tasklist/ps).
        proc.stdin.write(password + "\n")
        proc.stdin.flush()
    return proc


def start_reader_thread(proc, line_queue):
    def _pump():
        for line in proc.stdout:
            line_queue.put(line.rstrip("\n"))
        line_queue.put(None)
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    return t


def send(proc, cmd, log):
    line = f":{cmd}"
    log.append(f">>> {line}")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def drain(line_queue, log, seconds):
    end = time.time() + seconds
    lines = []
    while time.time() < end:
        try:
            line = line_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        log.append(line)
        lines.append(line)
    return lines


def stop_client(proc, grace_seconds=5):
    """Terminate the client and block until it (and its xvfb-run/Xvfb
    children) are actually gone, so the display number it used is safe to
    hand to the next job. proc.terminate() alone only signals the top-level
    xvfb-run shell, which doesn't reliably forward the signal to its java and
    Xvfb children, and doesn't wait for exit - both of which would leave the
    display's lock file/socket around for a subsequent job to race onto."""
    try:
        top = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    try:
        procs = top.children(recursive=True)
    except psutil.NoSuchProcess:
        procs = []
    procs.append(top)

    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _, alive = psutil.wait_procs(procs, timeout=grace_seconds)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(alive, timeout=grace_seconds)
    # Reap via Python's own handle too, so subprocess doesn't keep it a zombie.
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def exited_early(proc, log, phase):
    """Return an error outcome if the client exited before the scout finished."""
    exit_code = proc.poll()
    if exit_code is None:
        return None
    return {
        "result": f"ERROR: Hurricane client exited during {phase} (exit code {exit_code})",
        "found_roads": [],
        "log": log,
    }


def run_scout(bindir, user, char, roads, gob, server="game.havenandhearth.com",
              delays=None, verbose=False, password=None, display=None):
    """
    Runs one full scout cycle: login, then for each road in `roads` (checked
    in order): right-click the milestone, start travel, check for the gob,
    cancel movement, hearth home. Quits once every road has been checked.

    `roads` is a list of one or more road name strings.

    If `password` is provided, logs in with a fresh username+password
    handshake instead of relying on a previously cached login token.

    Returns a dict:
      {"result": "FOUND"|"NOTFOUND"|"ERROR: ...",
       "found_roads": [road names where the gob was seen],
       "log": [...]}
    """
    delays = {**DEFAULT_DELAYS, **(delays or {})}
    if isinstance(roads, str):
        roads = [roads]
    log = []
    line_queue = queue.Queue()
    found_roads = []
    proc = None

    try:
        proc = start_client(bindir, user, server, password=password, display=display)
        start_reader_thread(proc, line_queue)

        drain(line_queue, log, delays["boot"])
        outcome = exited_early(proc, log, "startup")
        if outcome:
            return outcome

        send(proc, f"play {char}", log)
        drain(line_queue, log, delays["login"])
        outcome = exited_early(proc, log, "login")
        if outcome:
            return outcome

        for road in roads:
            send(proc, "rclick milestone-stone-e", log)
            drain(line_queue, log, delays["approach"])
            outcome = exited_early(proc, log, "approaching the milestone")
            if outcome:
                return outcome

            send(proc, f"travelroad {road}", log)
            drain(line_queue, log, delays["travelapproach"])
            outcome = exited_early(proc, log, f"travelling to {road}")
            if outcome:
                return outcome

            send(proc, f"findgob {gob}", log)
            result_lines = drain(line_queue, log, delays["settle"])
            outcome = exited_early(proc, log, f"searching {road}")
            if outcome:
                return outcome
            found = any("FOUND:" in l and "NOTFOUND:" not in l for l in result_lines)
            if not found:
                more = drain(line_queue, log, 1.0)
                found = any("FOUND:" in l and "NOTFOUND:" not in l for l in more)
            if found:
                found_roads.append(road)

            send(proc, "cancelmove", log)
            drain(line_queue, log, delays["short"])
            outcome = exited_early(proc, log, "cancelling movement")
            if outcome:
                return outcome

            send(proc, "hearth", log)
            drain(line_queue, log, delays["teleport"])
            outcome = exited_early(proc, log, "returning home")
            if outcome:
                return outcome

        send(proc, "q", log)
        drain(line_queue, log, delays["short"])

        result = "FOUND" if found_roads else "NOTFOUND"
    except Exception as e:
        result = f"ERROR: {e}"
    finally:
        if proc is not None:
            stop_client(proc)

    if verbose:
        print("\n".join(log))

    return {"result": result, "found_roads": found_roads, "log": log}