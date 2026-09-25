#!/usr/bin/env python3
"""
scripts/aiven_token.py

Command-line management for the Aiven API token the app uses to check and
power on its Postgres service (see aiven_service.py / app.py). This is
deliberately a script, not a button in the app: creating or revoking an
account-wide API token is a real, hard-to-reverse action against your
Aiven account, and deserves a person running it on purpose from a
terminal, not a background check inside a web session doing it silently.

Needs AIVEN_API_TOKEN set (in .env or the real environment) to do anything.

Usage:
    python scripts/aiven_token.py validate
    python scripts/aiven_token.py list
    python scripts/aiven_token.py rotate --description "GameSheetScanner app"
    python scripts/aiven_token.py rotate --description "..." --max-age-days 365 --extend-when-used --write-env .env
    python scripts/aiven_token.py revoke <token_prefix> --yes

Rotating safely (two steps on purpose, so a bad new token can't lock you
out with no way back):
    1. `rotate` creates a new token and prints it once. Copy it into your
       .env as AIVEN_API_TOKEN, restart the app, and confirm it still
       works (e.g. `validate` again, or just watch the app connect).
    2. Once you've confirmed that, `revoke` the *old* token's prefix (or
       pass --revoke-old to `rotate` up front, if you're confident you
       won't need to roll back).
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import os  # noqa: E402

import aiven_service as av  # noqa: E402


def _token() -> str:
    token = os.environ.get("AIVEN_API_TOKEN")
    if not token:
        sys.exit("Error: set the AIVEN_API_TOKEN environment variable first.")
    return token


def _format_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except ValueError:
        return value


def cmd_validate(args: argparse.Namespace) -> None:
    token = _token()
    try:
        user = av.get_current_user(token)
    except av.AivenServiceError as e:
        print(f"INVALID: {e}")
        sys.exit(1)

    print(f"Valid — authenticates as {user.get('user_email') or user.get('email') or '(unknown user)'}.")

    try:
        info = av.find_token_info(token)
    except av.AivenServiceError as e:
        print(f"(Couldn't look up this token's own details: {e})")
        return

    if info is None:
        print("(Couldn't match this token to a specific entry in the account's token list.)")
        return

    print(f"  Description:   {info.get('description') or '—'}")
    print(f"  Token prefix:  {info.get('token_prefix') or '—'}")
    print(f"  Expires:       {_format_time(info.get('expiry_time'))}")
    print(f"  Last used:     {_format_time(info.get('last_used_time'))}")
    if info.get("last_ip"):
        print(f"  Last used from: {info['last_ip']}")


def cmd_list(args: argparse.Namespace) -> None:
    token = _token()
    tokens = av.list_access_tokens(token)
    if not tokens:
        print("No access tokens on this account.")
        return
    for t in tokens:
        current = " (this one)" if t.get("token_prefix") and token.startswith(t["token_prefix"]) else ""
        prefix = t.get("token_prefix") or "?"
        description = t.get("description") or ""
        print(
            f"  {prefix:20s} {description:35s} "
            f"expires: {_format_time(t.get('expiry_time')):20s} "
            f"last used: {_format_time(t.get('last_used_time'))}{current}"
        )


def _write_env_token(env_path: Path, new_token: str) -> None:
    """Replace (or append) the AIVEN_API_TOKEN= line in an env file in
    place, leaving every other line untouched. Used so a freshly-created
    secret can go straight to disk instead of through a terminal/chat
    transcript that might get logged somewhere."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    new_line = f"AIVEN_API_TOKEN={new_token}"
    for i, line in enumerate(lines):
        if line.startswith("AIVEN_API_TOKEN="):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_rotate(args: argparse.Namespace) -> None:
    token = _token()
    old_info = None
    try:
        old_info = av.find_token_info(token)
    except av.AivenServiceError:
        pass  # not fatal — rotation can proceed without knowing the old one's prefix

    max_age_seconds = args.max_age_days * 86400 if args.max_age_days else None
    new = av.create_access_token(
        token, description=args.description, max_age_seconds=max_age_seconds,
        extend_when_used=args.extend_when_used,
    )

    if args.write_env:
        env_path = Path(args.write_env)
        _write_env_token(env_path, new["full_token"])
        print(f"New token written to {env_path} (AIVEN_API_TOKEN updated) — not printed here.")
    else:
        print("New token created. Copy it now — Aiven will never show it again:\n")
        print(f"  AIVEN_API_TOKEN={new['full_token']}\n")

    print(f"Description: {new.get('description')}")
    print(f"Max age:     {args.max_age_days} day(s)" if args.max_age_days else "Max age:     none (doesn't expire)")
    print(f"Extend when used: {args.extend_when_used}")
    print(f"Expires:     {_format_time(new.get('expiry_time'))}")
    print(
        "\nRestart the app and confirm it works (e.g. `validate` again) before revoking the old token."
    )

    if args.revoke_old:
        if not old_info:
            print(
                "\n--revoke-old was passed, but the currently-configured token couldn't be "
                "identified in the account's token list — nothing revoked. Use `revoke "
                "<token_prefix>` manually once you know which one it is."
            )
            return
        if not args.yes:
            print(
                f"\n--revoke-old also needs --yes to confirm revoking the OLD token "
                f"({old_info.get('token_prefix')}, {old_info.get('description')}) right now — "
                "skipped. Run `revoke` separately once you've confirmed the new token works."
            )
            return
        av.revoke_access_token(new["full_token"], old_info["token_prefix"])
        print(f"\nRevoked the old token ({old_info.get('token_prefix')}).")


def cmd_revoke(args: argparse.Namespace) -> None:
    token = _token()
    if not args.yes:
        sys.exit(
            f"Refusing to revoke '{args.token_prefix}' without --yes — this is permanent, and "
            "anything still using that token (including this app, if it's the current one) "
            "loses access immediately."
        )
    av.revoke_access_token(token, args.token_prefix)
    print(f"Revoked '{args.token_prefix}'.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Confirm the configured AIVEN_API_TOKEN still works")
    sub.add_parser("list", help="List every access token on the account (metadata only)")

    p_rotate = sub.add_parser("rotate", help="Create a new token (does not touch the old one, unless --revoke-old)")
    p_rotate.add_argument("--description", required=True, help="What this token is for, e.g. 'GameSheetScanner app'")
    p_rotate.add_argument("--max-age-days", type=int, help="Expire the new token after this many days (default: never)")
    p_rotate.add_argument(
        "--extend-when-used", action="store_true", help="Push the expiry back further each time the token is used"
    )
    p_rotate.add_argument(
        "--revoke-old", action="store_true",
        help="Also revoke the currently-configured token immediately (needs --yes too)",
    )
    p_rotate.add_argument("--yes", action="store_true", help="Confirm --revoke-old (required, not assumed)")
    p_rotate.add_argument(
        "--write-env", metavar="PATH",
        help="Write the new secret straight into this env file's AIVEN_API_TOKEN= line instead of "
             "printing it (e.g. --write-env .env)",
    )

    p_revoke = sub.add_parser("revoke", help="Permanently revoke a specific token")
    p_revoke.add_argument("token_prefix", help="From `list` — the prefix (or full token) identifying it")
    p_revoke.add_argument("--yes", action="store_true", help="Confirm — required, not assumed")

    args = parser.parse_args()
    {
        "validate": cmd_validate,
        "list": cmd_list,
        "rotate": cmd_rotate,
        "revoke": cmd_revoke,
    }[args.command](args)


if __name__ == "__main__":
    main()
