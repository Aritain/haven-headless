# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask dashboard + background scheduler that drives a headless, console-controlled build of the
*Haven & Hearth* game client (`Hurricane/`) to repeatedly log in as configured characters, travel
specific roads, and check for a target creature ("gob"). On a match it notifies via Discord webhook.
`Hurricane/` is the actual (LGPL) game client source, vendored in this repo with one custom addition:
`Hurricane/src/haven/HeadlessClient.java`, a console-driven entry point (no real window, driven by
stdin/stdout commands) built specifically for this bot.

## Commands

```bash
# Python deps
pip install -r Scripts/requirements.txt

# Run the dashboard directly (needs a real/virtual X server + java 17, and
# Settings > bindir pointed at a built Hurricane/bin)
cd Scripts && python3 app.py

# Docker (what actually runs in prod): pulls latest git, rebuilds image, recreates container
./deploy.sh
# equivalent manually:
docker compose up -d --build --force-recreate

# Optional: tune per-host timing via a .env file (gitignored) next to
# docker-compose.yml, e.g.:
echo "DELAY_MULTIPLIER=3" > .env

# One-off scout run from the CLI, outside the scheduler/dashboard
python3 Scripts/scout.py --bindir "Hurricane/bin" --user USER --char CHAR \
  --road ROADNAME --gob GOBNAME [--road ROADNAME2 ...] [--verbose]

# Rebuild the Java client after editing anything in Hurricane/src
cd Hurricane && ant
# -> regenerates Hurricane/bin/hafen.jar. REQUIRED after any Hurricane/src
#    change: Docker's build does NOT compile Java, it only COPYs whatever is
#    already sitting in Hurricane/bin (which is gitignored - a local,
#    uncommitted build artifact). No CI/build step does this for you.
#    Needs javac + ant on the host; not installed by default (`apt-get
#    install openjdk-17-jdk ant`).
```

No test suite exists in this repo.

## Architecture

**Scripts/** (the actual app, everything Python):
- `app.py` - Flask routes/dashboard (jobs, accounts, characters, settings, logs, auth). Calls
  `scheduler.start()` and `watchdog.start()` on boot (`create_app()`).
- `scheduler.py` - background poll loop (`POLL_SECONDS`) that finds due jobs and runs them via a
  `ThreadPoolExecutor` capped at `MAX_PARALLEL`. One lock per `account_id` so the same account never
  runs two characters concurrently. Also owns a fixed pool of X display numbers (`DISPLAY_BASE` +
  `MAX_PARALLEL`), handed out per job instead of trusting `xvfb-run -a`'s own (non-atomic, racy under
  concurrency) auto-pick.
- `scout_lib.py` - the actual client driver, shared by `scout.py` (CLI) and `scheduler.py`/`app.py`
  (web). `run_scout()` launches `xvfb-run ... java ... haven.HeadlessClient` as a subprocess, logs in,
  drives it via a tiny scripted stdin console-command protocol (`:play`, `:rclick`, `:travelroad`,
  `:findgob`, `:cancelmove`, `:hearth`, `:q` - see `HeadlessClient.java`'s `cmdmap`), and parses
  stdout for `FOUND:`/`NOTFOUND:` markers. Between each command it just sleeps a fixed delay
  (`DEFAULT_DELAYS`: `boot`, `login`, `approach`, `travelapproach`, `settle`, `short`, `teleport`)
  instead of waiting for any actual readiness signal from the client - too short on a slow
  host/network path to the game server, this silently no-ops every remaining step (`"Not in game
  yet"`, `"No road menu found"`, etc.) rather than erroring, which reads as a false
  NOTFOUND/never-found-anything rather than an obvious failure. The `boot`/`login`/`approach`/
  `travelapproach` delays scale by the `DELAY_MULTIPLIER` env var (default `1.0`; set via a
  gitignored `.env` file per-host, see Commands) for exactly this reason. `stop_client()` does a full recursive
  terminate-then-wait-then-kill of the whole `xvfb-run`/`java`/`Xvfb` process tree, since a bare
  `proc.terminate()` only signals the top-level `xvfb-run` shell and isn't reliably forwarded to its
  children.
- `watchdog.py` - background thread that reaps `haven.HeadlessClient` processes that are either
  orphaned (parent died) or stuck (`age > MAX_RUN_SECONDS`). **Orphan-killing is currently disabled**
  (see Known issues below) - only stuck-process reaping is active.
- `db.py` - sqlite (accounts, characters, jobs, job_roads, settings, users, activity_log). DB path is
  `$APP_DATA_DIR/scout.db` (mounted as a Docker volume in prod so state survives redeploys).
- `crypto_util.py` - Fernet-encrypts account passwords at rest, key at `$APP_DATA_DIR/crypto_key.txt`.
- `notifier.py` - Discord webhook on a found gob.

**Hurricane/** - vendored Haven & Hearth client source. `Hurricane/bin/` (built jars, gitignored) is
what actually runs; it's a local build artifact, not part of the repo or the Docker build. Rebuild
with `ant` after any source change (see Commands).

**Deployment**: `Dockerfile` is `python:3.12-slim-bookworm` + `openjdk-17-jre` + `xvfb`/`xauth`,
copies `Scripts/` and `Hurricane/` (including whatever's already in `Hurricane/bin`) into the image.
`docker-compose.yml` sets `mem_limit`, `shm_size`, and (currently, temporarily) `cap_add: SYS_PTRACE`
for `strace`-based diagnosis. State lives in the `haven_headless_data` volume (`APP_DATA_DIR=/data`).

## Why a headless game client needs Xvfb and still crashes sometimes

`HeadlessClient` never shows a window, but Hurricane's rendering pipeline (JOGL/GLX, via
`haven.iosys.tk.JOGLOffscreen`) still needs a real (if virtual) X server to create a GL context, so
every run wraps `java` in `xvfb-run`. This combination is fragile: JogAmp's X11 I/O error handler
treats any connection-level hiccup as fatal and aborts the whole JVM
(`Nativewindow X11 IOError ... FATAL ERROR in native method`). Several theories were investigated and
ruled out across one long debugging session (JVM heap/GC tuning, `/dev/shm` size, render-target size,
concurrency/display-number collisions, `java.awt.headless`, forcing alternate `haven.iosys.tk.Toolkit`
implementations) - see git history around the "mem limits", "limit resolution + fixes", and "fix
python" commits for the abandoned attempts. **All were reverted**; `Hurricane/src` is currently back
to vanilla.

## Fixed issue: HeadlessClient stdin/loop startup race

`HeadlessClient.run()` used to start the stdio-reader thread *before* assigning `this.loop`:

```java
Thread stdio = new HackThread(this::stdin, "stdio reader");
stdio.start();
UILoop loop = this.loop = new HeadlessLoop();
loop.start();
```

`stdin()` reads `loop.ui` (an implicit `this.loop`) with no null-check, outside the only try/catch
in that method (which only catches `IOException`, not `NullPointerException`). If a command was
already sitting in the stdin pipe buffer (`scout_lib.py` sends `:play {char}` after a fixed delay,
not after any real readiness signal) at the moment the new thread got scheduled, it would read and
process that command before the very next line assigned `this.loop` - NPE, uncaught, and
`haven.error.SimpleHandler("Haven main group", true)` treats that as fatal and kills the whole JVM
mid-login (seen as `exit code 127` with `Cannot read field "ui" because "this.loop" is null`).

Whether this triggers is purely down to that host's thread-scheduling behavior on `Thread.start()`
- consistently fine on some machines, consistently fatal on others (e.g. shared/constrained vCPU
hosts), never intermittent on a given host. Fixed by reordering so `this.loop` is assigned and
`loop.start()` called *before* the stdio thread is created, closing the window entirely:

```java
UILoop loop = this.loop = new HeadlessLoop();
loop.start();
Thread stdio = new HackThread(this::stdin, "stdio reader");
stdio.start();
```

Note: `HeadlessClient.java` lives under `Hurricane/`, which is entirely gitignored (see
`.gitignore`) - this fix (and any future edits here) must be applied by hand on each host and does
not travel via `git pull`. Rebuild with `ant` after applying (see Commands) and restart the
container - no image rebuild needed since `Hurricane/` is bind-mounted, not baked into the image.

## Known issue: watchdog orphan-detection false positives

`watchdog.py`'s orphan check (`proc.ppid() == 1`) was, at least once, confirmed to kill a live,
still-legitimately-parented `HeadlessClient` process only ~3 seconds after it started - not a
genuinely orphaned one. Every X11-crash-looking failure investigated in the same session had a
`watchdog orphan_killed` log entry within milliseconds of it; disabling orphan-kill entirely made a
previously near-100%-failing job succeed cleanly. The mechanism (why `xvfb-run`'s wrapper shell, which
should stay alive as `java`'s parent for the whole run, would ever show `ppid()==1` for a live
process) was not root-caused. Orphan-kill is disabled (`scan_once()` hardcodes `orphaned = False`)
until it is; stuck-process reaping (`age > MAX_RUN_SECONDS`) is still active as the memory-leak
backstop. Before re-enabling orphan-kill, reproduce the false positive and understand *why* before
trusting `_is_orphaned()` again - don't just re-enable it.
