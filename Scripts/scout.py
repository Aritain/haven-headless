#!/usr/bin/env python3
"""
scout.py - run one headless-client scouting cycle from the command line.

Usage:
    python scout.py --bindir "C:\\path\\to\\Hurricane\\bin" \
                     --user saltbae4 --char anglerbot \
                     --road Winnfield --gob mammoth --verbose
"""

import argparse
from scout_lib import run_scout, DEFAULT_DELAYS


def main():
    ap = argparse.ArgumentParser(description="Run one Haven & Hearth headless scout cycle.")
    ap.add_argument("--bindir", required=True, help=r'Path to Hurricane\bin folder')
    ap.add_argument("--user", required=True, help="Account username")
    ap.add_argument("--char", required=True, help="Character name")
    ap.add_argument("--road", required=True, action="append",
                     help="Road name at the milestone. Repeat --road for multiple roads.")
    ap.add_argument("--gob", required=True, help="Gob name fragment to search for")
    ap.add_argument("--server", default="game.havenandhearth.com")
    ap.add_argument("--verbose", action="store_true")

    ap.add_argument("--d-boot", type=float, default=DEFAULT_DELAYS["boot"])
    ap.add_argument("--d-login", type=float, default=DEFAULT_DELAYS["login"])
    ap.add_argument("--d-approach", type=float, default=DEFAULT_DELAYS["approach"])
    ap.add_argument("--d-travelapproach", type=float, default=DEFAULT_DELAYS["travelapproach"])
    ap.add_argument("--d-settle", type=float, default=DEFAULT_DELAYS["settle"])
    ap.add_argument("--d-short", type=float, default=DEFAULT_DELAYS["short"])
    ap.add_argument("--d-teleport", type=float, default=DEFAULT_DELAYS["teleport"])

    args = ap.parse_args()

    delays = {
        "boot": args.d_boot,
        "login": args.d_login,
        "approach": args.d_approach,
        "travelapproach": args.d_travelapproach,
        "settle": args.d_settle,
        "short": args.d_short,
        "teleport": args.d_teleport,
    }

    outcome = run_scout(args.bindir, args.user, args.char, args.road, args.gob,
                         args.server, delays, args.verbose)
    if args.verbose:
        print("\n".join(outcome["log"]))
    print(f"RESULT: {outcome['result']}")


if __name__ == "__main__":
    main()
