#!/usr/bin/env python3
"""
scripts/aiven_ip_filter.py

Command-line management for the Aiven Postgres service's ip_filter — which
client IPs may even reach the database port at all (separate from the API
access-token IP allowlist handled by scripts/aiven_token.py, which gates
the Management API, not the database). A freshly-created Aiven service
defaults this wide open (0.0.0.0/0 and ::/0), which this exists to tighten.

This app is typically reached from Streamlit Community Cloud's published
outbound IPs (https://docs.streamlit.io/deploy/streamlit-community-cloud/status)
plus, optionally, a fixed set of "extra" CIDRs for direct/admin access (e.g.
this dev machine, for scripts/manage_users.py or a direct psql session).
Streamlit's docs explicitly warn that list "may change at any time without
notice" and there's no API for it — only this same page to re-check.

Needs AIVEN_API_TOKEN, AIVEN_PROJECT_NAME, and AIVEN_SERVICE_NAME set (in
.env or the real environment).

Usage:
    python scripts/aiven_ip_filter.py show
    python scripts/aiven_ip_filter.py check --extra-cidr 1.2.3.4/32
    python scripts/aiven_ip_filter.py sync --extra-cidr 1.2.3.4/32 --extra-cidr 2600:...::/64 --yes

`check` exits 0 if the filter already covers every current Streamlit IP
(plus any --extra-cidr given) and has nothing else in it, 1 otherwise —
usable as a periodic cron job / monitoring check. `sync` requires --yes and
always prints the exact before/after diff first, since this can lock out
anything not included (this app's own database access included) the
moment it's applied.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import os  # noqa: E402

import aiven_service as av  # noqa: E402


def _config() -> tuple[str, str, str]:
    token = os.environ.get("AIVEN_API_TOKEN")
    project = os.environ.get("AIVEN_PROJECT_NAME")
    service = os.environ.get("AIVEN_SERVICE_NAME")
    missing = [
        name for name, value in
        [("AIVEN_API_TOKEN", token), ("AIVEN_PROJECT_NAME", project), ("AIVEN_SERVICE_NAME", service)]
        if not value
    ]
    if missing:
        sys.exit(f"Error: set {', '.join(missing)} first.")
    return token, project, service


def cmd_show(args: argparse.Namespace) -> None:
    token, project, service = _config()
    current = av.get_service_ip_filter(token, project, service)
    if not current:
        print("ip_filter is empty — that means NO IP can connect (not wide open; the opposite).")
        return
    for cidr in sorted(current):
        flag = "  <-- allows literally any address" if cidr in ("0.0.0.0/0", "::/0") else ""
        print(f"  {cidr}{flag}")


def cmd_check(args: argparse.Namespace) -> None:
    token, project, service = _config()
    try:
        diff = av.diff_ip_filter_against_streamlit(token, project, service, extra_cidrs=args.extra_cidr)
    except av.AivenServiceError as e:
        print(f"Couldn't check: {e}")
        sys.exit(1)

    if diff["in_sync"]:
        print("In sync — ip_filter exactly matches Streamlit's current IPs (+ any --extra-cidr given).")
        return

    if diff["missing"]:
        print(f"MISSING ({len(diff['missing'])}) — not currently allowed, so the app may not reach its own DB:")
        for cidr in diff["missing"]:
            print(f"  {cidr}")
    if diff["extra"]:
        print(f"EXTRA ({len(diff['extra'])}) — currently allowed but not a Streamlit IP or --extra-cidr:")
        for cidr in diff["extra"]:
            flag = "  <-- allows literally any address" if cidr in ("0.0.0.0/0", "::/0") else ""
            print(f"  {cidr}{flag}")
    sys.exit(1)


def cmd_sync(args: argparse.Namespace) -> None:
    token, project, service = _config()
    streamlit_ips = av.get_streamlit_community_cloud_ips()
    wanted = sorted(set(streamlit_ips) | set(args.extra_cidr))
    current = sorted(av.get_service_ip_filter(token, project, service))

    added = sorted(set(wanted) - set(current))
    removed = sorted(set(current) - set(wanted))

    print(f"Current ip_filter ({len(current)} entries):")
    for cidr in current:
        print(f"  {cidr}")
    print(f"\nProposed ip_filter ({len(wanted)} entries) = {len(streamlit_ips)} Streamlit IPs"
          f" + {len(args.extra_cidr)} --extra-cidr:")
    for cidr in wanted:
        print(f"  {cidr}")
    print(f"\nWould ADD:   {added or '(none)'}")
    print(f"Would REMOVE: {removed or '(none)'}")

    if any(cidr in removed for cidr in ("0.0.0.0/0", "::/0")):
        print(
            "\nNote: this removes the current wide-open wildcard — after this, ONLY the addresses "
            "listed above (and none other) will be able to reach the database."
        )

    if not args.yes:
        print("\nRe-run with --yes to apply this.")
        return

    result = av.set_service_ip_filter(token, project, service, wanted)
    print(f"\nApplied. ip_filter now has {len(result)} entries.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="Print the service's current ip_filter")

    p_check = sub.add_parser(
        "check", help="Compare current ip_filter against Streamlit's published IPs (exit 1 if out of sync)"
    )
    p_check.add_argument(
        "--extra-cidr", action="append", default=[],
        help="A CIDR that should also be allowed (e.g. an admin IP) — repeatable",
    )

    p_sync = sub.add_parser("sync", help="Update ip_filter to match Streamlit's IPs (+ --extra-cidr); needs --yes")
    p_sync.add_argument(
        "--extra-cidr", action="append", default=[],
        help="A CIDR that should also be allowed (e.g. an admin IP) — repeatable, always preserved by sync",
    )
    p_sync.add_argument("--yes", action="store_true", help="Actually apply the change (otherwise just show the diff)")

    args = parser.parse_args()
    {
        "show": cmd_show,
        "check": cmd_check,
        "sync": cmd_sync,
    }[args.command](args)


if __name__ == "__main__":
    main()
