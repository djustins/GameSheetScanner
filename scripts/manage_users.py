#!/usr/bin/env python3
"""
scripts/manage_users.py

Command-line user management for the app's login system. Mainly for
bootstrapping the first admin account — nobody can reach the app's User
Management tab before at least one admin exists, so that tab can't create
its own first user — and for emergency access if every admin gets locked
out. Day-to-day user and role management should go through the app's User
Management tab instead.

Usage:
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require

    python scripts/manage_users.py add admin@example.com --admin
    python scripts/manage_users.py add-role Coach --pages process,edit,standings
    python scripts/manage_users.py add coach@example.com --role Coach
    python scripts/manage_users.py list
    python scripts/manage_users.py list-roles
    python scripts/manage_users.py set-role coach@example.com Coach
    python scripts/manage_users.py set-password user@example.com
    python scripts/manage_users.py deactivate user@example.com
    python scripts/manage_users.py reactivate user@example.com
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game_sheet_core as core  # noqa: E402


def _connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Error: set the DATABASE_URL environment variable first.")
    return core.init_db(dsn)


def _prompt_password(label: str) -> str:
    password = getpass.getpass(f"{label}: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        sys.exit("Passwords didn't match.")
    if not password:
        sys.exit("Password can't be empty.")
    return password


def _find_role(conn, name: str) -> dict:
    match = next((r for r in core.list_roles(conn) if r["name"].lower() == name.lower()), None)
    if not match:
        sys.exit(f"No role named {name!r}. See `list-roles` for what exists.")
    return match


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Create a new user")
    p_add.add_argument("email")
    p_add.add_argument("--admin", action="store_true", help="Grant full admin access (all pages + User Management)")
    p_add.add_argument("--name", help="Display name")
    p_add.add_argument("--role", help="Role name to assign (see `list-roles`); ignored with --admin")

    sub.add_parser("list", help="List all users")

    p_pw = sub.add_parser("set-password", help="Set/reset a user's password")
    p_pw.add_argument("email")

    p_role = sub.add_parser("set-role", help="Assign (or clear) a user's role")
    p_role.add_argument("email")
    p_role.add_argument("role", help="Role name, or 'none' to clear it")

    p_deact = sub.add_parser("deactivate", help="Deactivate a user (can no longer log in)")
    p_deact.add_argument("email")

    p_react = sub.add_parser("reactivate", help="Reactivate a previously deactivated user")
    p_react.add_argument("email")

    p_add_role = sub.add_parser("add-role", help="Create a new role")
    p_add_role.add_argument("name")
    p_add_role.add_argument("--pages", help=f"Comma-separated page keys: {', '.join(core.PAGES)}")

    sub.add_parser("list-roles", help="List all roles and the pages each grants")

    p_del_role = sub.add_parser("delete-role", help="Delete a role (users with it lose page access)")
    p_del_role.add_argument("name")

    args = parser.parse_args()
    conn = _connect()

    if args.command == "list":
        users = core.list_users(conn, include_deleted=True)
        if not users:
            print("No users yet.")
            return
        for u in users:
            status = "deactivated" if u["deleted_at"] else "active"
            access = "admin (all pages)" if u["is_admin"] else (", ".join(u["pages"]) or "(no role/pages)")
            print(f"  {u['email']:30s} {status:12s} {access}")
        return

    if args.command == "list-roles":
        roles = core.list_roles(conn)
        if not roles:
            print("No roles yet.")
            return
        for r in roles:
            print(f"  {r['name']:20s} {', '.join(r['pages']) or '(no pages)'}")
        return

    if args.command == "add-role":
        pages = [p.strip() for p in args.pages.split(",")] if args.pages else []
        invalid = [p for p in pages if p not in core.PAGES]
        if invalid:
            sys.exit(f"Unknown page(s): {', '.join(invalid)}. Valid pages: {', '.join(core.PAGES)}")
        role_id = core.add_role(conn, args.name, pages=pages)
        print(f"Created role #{role_id} ({args.name}).")
        return

    if args.command == "delete-role":
        role = _find_role(conn, args.name)
        core.delete_role(conn, role["id"])
        print(f"Deleted role {args.name}. Anyone who had it now has no page access.")
        return

    if args.command == "add":
        if core.get_user_by_email(conn, args.email):
            sys.exit(f"A user with email {args.email} already exists.")
        role_id = _find_role(conn, args.role)["id"] if args.role else None
        password = _prompt_password(f"Set a password for {args.email}")
        user_id = core.add_user(
            conn, args.email, password, display_name=args.name, is_admin=args.admin, role_id=role_id
        )
        print(f"Created user #{user_id} ({args.email}){' as admin' if args.admin else ''}.")
        return

    # Remaining commands operate on an existing user.
    target = core.get_user_by_email(conn, args.email)
    if not target:
        sys.exit(f"No user found with email {args.email}.")

    if args.command == "set-password":
        password = _prompt_password(f"New password for {args.email}")
        core.set_user_password(conn, target["id"], password)
        print(f"Password updated for {args.email}.")
    elif args.command == "set-role":
        if args.role.lower() == "none":
            core.set_user_role(conn, target["id"], None)
            print(f"Cleared {args.email}'s role.")
        else:
            role = _find_role(conn, args.role)
            core.set_user_role(conn, target["id"], role["id"])
            print(f"Set {args.email}'s role to {role['name']}.")
    elif args.command == "deactivate":
        core.soft_delete_user(conn, target["id"])
        print(f"Deactivated {args.email}.")
    elif args.command == "reactivate":
        core.restore_user(conn, target["id"])
        print(f"Reactivated {args.email}.")


if __name__ == "__main__":
    main()
