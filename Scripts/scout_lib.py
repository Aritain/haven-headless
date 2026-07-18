"""
scout_lib.py - core logic for driving one headless-client scouting run.

Shared by scout.py (CLI) and app.py (web dashboard) so there's a single
tested implementation instead of two copies drifting apart.
"""

import queue
import subprocess
import threading
import time

JAVA_ARGS = [
    "--add-exports=java.base/java.lang=ALL-UNNAMED",
    "--add-exports=java.desktop/sun.awt=ALL-UNNAMED",
    "--add-exports=java.desktop/sun.java2d=ALL-UNNAMED",
    "--enable-native-access=ALL-UNNAMED",
    "-cp", "*",
    "haven.HeadlessClient",
]

DEFAULT_DELAYS = {
    "boot": 6.0,
    "login": 8.0,
    "approach": 4.0,
    "travelapproach": 8.0,
    "settle": 3.0,
    "short": 1.5,
    "teleport": 5.0,  # used for :hearth
}


def start_client(bindir, user, server, password=None):
    if password is not None:
        cmd = ["java", *JAVA_ARGS, "-u", user, "-w", server]
    else:
        cmd = ["java", *JAVA_ARGS, "-u", user, server]
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


def run_scout(bindir, user, char, roads, gob, server="game.havenandhearth.com",
              delays=None, verbose=False, password=None):
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

    proc = start_client(bindir, user, server, password=password)
    start_reader_thread(proc, line_queue)

    try:
        drain(line_queue, log, delays["boot"])

        send(proc, f"play {char}", log)
        drain(line_queue, log, delays["login"])

        for road in roads:
            send(proc, "rclick milestone-stone-e", log)
            drain(line_queue, log, delays["approach"])

            send(proc, f"travelroad {road}", log)
            drain(line_queue, log, delays["travelapproach"])

            send(proc, f"findgob {gob}", log)
            result_lines = drain(line_queue, log, delays["settle"])
            found = any("FOUND:" in l and "NOTFOUND:" not in l for l in result_lines)
            if not found:
                more = drain(line_queue, log, 1.0)
                found = any("FOUND:" in l and "NOTFOUND:" not in l for l in more)
            if found:
                found_roads.append(road)

            send(proc, "cancelmove", log)
            drain(line_queue, log, delays["short"])

            send(proc, "hearth", log)
            drain(line_queue, log, delays["teleport"])

        send(proc, "q", log)
        drain(line_queue, log, delays["short"])

        result = "FOUND" if found_roads else "NOTFOUND"
    except Exception as e:
        result = f"ERROR: {e}"
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    if verbose:
        print("\n".join(log))

    return {"result": result, "found_roads": found_roads, "log": log}
