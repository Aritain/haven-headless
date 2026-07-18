#!/usr/bin/env python3
"""
scout.py - drives one headless-client scouting run.

Launches the Java HeadlessClient as a subprocess, feeds it console
commands over stdin with delays between steps, and watches stdout for
FOUND:/NOTFOUND: lines from the :findgob command.

Usage:
    python scout.py --bindir "C:\\path\\to\\Hurricane\\bin" ^
                     --user saltbae4 --char anglerbot ^
                     --road Winnfield --gob mammoth

Exit behavior:
    Prints a final line starting with RESULT: FOUND or RESULT: NOTFOUND
    (or RESULT: ERROR on failure), so a calling process can just check
    the last line of output.
"""

import argparse
import queue
import subprocess
import sys
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


def start_client(bindir, user, server):
    """Launch the headless client, cwd'd into the bin folder."""
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
    return proc


def start_reader_thread(proc, line_queue):
    """Continuously read stdout into a queue so sending input never blocks."""
    def _pump():
        for line in proc.stdout:
            line_queue.put(line.rstrip("\n"))
        line_queue.put(None)  # signal EOF
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    return t


def send(proc, cmd, log):
    line = f":{cmd}"
    log.append(f">>> {line}")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def drain(line_queue, log, seconds):
    """Collect any output that arrives over the next `seconds`."""
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


def run_scout(bindir, user, char, road, gob, server, delays, verbose):
    log = []
    line_queue = queue.Queue()

    proc = start_client(bindir, user, server)
    start_reader_thread(proc, line_queue)

    try:
        drain(line_queue, log, delays["boot"])

        send(proc, f"play {char}", log)
        drain(line_queue, log, delays["login"])

        send(proc, "rclick milestone-stone-e", log)
        drain(line_queue, log, delays["approach"])

        send(proc, f"travelroad {road}", log)
        drain(line_queue, log, delays["travelapproach"])

        send(proc, "confirmclick", log)
        drain(line_queue, log, delays["teleport"])

        send(proc, f"findgob {gob}", log)
        result_lines = drain(line_queue, log, delays["settle"])

        found = any(l.startswith("FOUND:") for l in result_lines)
        # also catch a slightly later arrival, just in case
        if not found:
            more = drain(line_queue, log, 1.0)
            found = any(l.startswith("FOUND:") for l in more)

        send(proc, "cancelmove", log)
        drain(line_queue, log, delays["short"])

        send(proc, "hearth", log)
        drain(line_queue, log, delays["teleport"])

        send(proc, "q", log)
        drain(line_queue, log, delays["short"])

        result = "FOUND" if found else "NOTFOUND"
    except Exception as e:
        result = f"ERROR: {e}"
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    if verbose:
        print("\n".join(log))

    print(f"RESULT: {result}")
    return result


def main():
    ap = argparse.ArgumentParser(description="Run one Haven & Hearth headless scout cycle.")
    ap.add_argument("--bindir", required=True, help=r'Path to Hurricane\bin folder')
    ap.add_argument("--user", required=True, help="Account username")
    ap.add_argument("--char", required=True, help="Character name")
    ap.add_argument("--road", required=True, help="Road name at the milestone")
    ap.add_argument("--gob", required=True, help="Gob name fragment to search for")
    ap.add_argument("--server", default="game.havenandhearth.com")
    ap.add_argument("--verbose", action="store_true", help="Print full command/response log")

    # Delay tuning - override if steps need more/less time on your connection
    ap.add_argument("--d-boot", type=float, default=6.0, help="Wait after launch before login")
    ap.add_argument("--d-login", type=float, default=8.0, help="Wait after :play for world load")
    ap.add_argument("--d-approach", type=float, default=4.0, help="Wait after :rclick for walk+menu")
    ap.add_argument("--d-travelapproach", type=float, default=3.0, help="Wait after :travelroad")
    ap.add_argument("--d-teleport", type=float, default=5.0, help="Wait after :confirmclick / :hearth")
    ap.add_argument("--d-settle", type=float, default=3.0, help="Wait after :findgob for objects to load")
    ap.add_argument("--d-short", type=float, default=1.5, help="Short generic wait")

    args = ap.parse_args()

    delays = {
        "boot": args.d_boot,
        "login": args.d_login,
        "approach": args.d_approach,
        "travelapproach": args.d_travelapproach,
        "teleport": args.d_teleport,
        "settle": args.d_settle,
        "short": args.d_short,
    }

    run_scout(args.bindir, args.user, args.char, args.road, args.gob, args.server, delays, args.verbose)


if __name__ == "__main__":
    main()
