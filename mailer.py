"""
mailer.py

Everything the app emails, sent through Resend (https://resend.com) from an
address on the league's own domain:

  - Messages an admin writes to a division's or some teams' parents and/or
    coaches (division_recipients + send).
  - Account emails: an invitation to set a password, and a reset link for a
    forgotten one (create_password_token / use_password_token).
  - Automatic notices to the admins, e.g. what the nightly league-site sync
    brought in (notify_admins).

Configuration is three environment variables on the API server:

  RESEND_API_KEY  the Resend API key
  EMAIL_FROM      the from address, e.g. "Team Pittsburgh <noreply@tptm.io>"
                  -- its domain must be verified in Resend
  APP_URL         the React app's address, for links in emails (optional:
                  account emails fall back to wherever the request came from)

Without the first two nothing is sent: is_configured() is False and send()
raises MailNotConfigured, so the rest of the app carries on without email.

Every send is recorded in email_log, successful or not. Each recipient gets
their own copy, so nobody sees anyone else's address.
"""

import hashlib
import html
import json
import os
import re
import secrets
import urllib.error
import urllib.request

import game_sheet_core as core

RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
BATCH_SIZE = 100  # Resend's limit per batch call

RESET_HOURS = 2
INVITE_DAYS = 7
MIN_PASSWORD_LENGTH = 8


class MailError(Exception):
    """The email service refused or couldn't be reached."""


class MailNotConfigured(MailError):
    """RESEND_API_KEY / EMAIL_FROM aren't set on this server."""


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY") and os.environ.get("EMAIL_FROM"))


def from_address() -> str | None:
    return os.environ.get("EMAIL_FROM") or None


def app_url(fallback: str | None = None) -> str:
    """Where the React app lives, without a trailing slash."""
    return (os.environ.get("APP_URL") or fallback or "").rstrip("/")


def text_to_html(text: str) -> str:
    """Plain text as simple HTML: blank lines separate paragraphs, single
    line breaks are kept, and bare links become clickable."""
    def paragraph(block: str) -> str:
        escaped = html.escape(block.strip())
        linked = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', escaped)
        return "<p>" + linked.replace("\n", "<br>") + "</p>"

    body = "".join(paragraph(b) for b in re.split(r"\n\s*\n", text.strip()) if b.strip())
    return f'<div style="font-family: system-ui, Segoe UI, Arial, sans-serif; font-size: 15px; line-height: 1.5">{body}</div>'


def _post_batch(messages: list[dict]) -> None:
    """One call to Resend's batch endpoint. Raises MailError on any failure."""
    request = urllib.request.Request(
        RESEND_BATCH_URL,
        data=json.dumps(messages).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            # Resend's API sits behind a filter that turns away urllib's default agent.
            "User-Agent": "team-pittsburgh-team-manager/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except ValueError:
            pass
        raise MailError(f"The email service refused the message ({e.code}): {detail}") from e
    except OSError as e:
        raise MailError(f"Couldn't reach the email service: {e}") from e


def send(
    conn, *, to: list[str], subject: str, text: str, kind: str,
    sent_by: int | None = None, reply_to: str | None = None,
    division_id: int | None = None, audience: str | None = None, post=None,
) -> dict:
    """Sends one message to every address in `to`, each as its own email, and
    records it in email_log. Returns {"sent": n, "failed": n, "error": str |
    None}; raises MailNotConfigured (nothing sent, nothing logged) if email
    isn't set up. `kind` is "message", "test", "invite", "password_reset" or
    "notice". `post` replaces the function that talks to Resend (_post_batch)."""
    if not is_configured():
        raise MailNotConfigured("Email isn't set up on this server yet.")
    addresses = list(dict.fromkeys(a.strip().lower() for a in to if a and "@" in a))
    post = post or _post_batch
    body_html = text_to_html(text)
    sent, failed, error = 0, 0, None
    for start in range(0, len(addresses), BATCH_SIZE):
        chunk = addresses[start:start + BATCH_SIZE]
        messages = [
            {
                "from": from_address(), "to": [address], "subject": subject, "text": text, "html": body_html,
                **({"reply_to": reply_to} if reply_to else {}),
            }
            for address in chunk
        ]
        try:
            post(messages)
            sent += len(chunk)
        except MailError as e:
            failed += len(chunk)
            error = str(e)
    conn.execute(
        """INSERT INTO email_log (kind, sent_by, subject, body, division_id, audience, recipients, sent, failed, error)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (kind, sent_by, subject, text, division_id, audience, json.dumps(addresses), sent, failed, error),
    )
    conn.commit()
    return {"sent": sent, "failed": failed, "error": error}


def list_email_log(conn, limit: int = 50) -> list[dict]:
    """The most recent sends, newest first. Account emails are left out of
    `body`: theirs contains a sign-in link."""
    rows = conn.execute(
        """SELECT l.id, l.kind, l.subject, l.body, l.audience, l.recipients, l.sent, l.failed, l.error,
                  l.created_at, u.display_name, u.email, d.year, d.season, d.age_group
           FROM email_log l
           LEFT JOIN users u ON u.id = l.sent_by
           LEFT JOIN divisions d ON d.id = l.division_id
           ORDER BY l.created_at DESC, l.id DESC LIMIT %s""",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0], "kind": r[1], "subject": r[2],
            "body": None if r[1] in ("invite", "password_reset") else r[3],
            "audience": r[4], "recipients": r[5] or [], "sent": r[6], "failed": r[7], "error": r[8],
            "created_at": r[9].isoformat(), "sent_by": r[10] or r[11],
            "division": f"{r[12]} {core.display_text(r[13])} — {r[14]}" if r[12] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Who a message goes to
# ---------------------------------------------------------------------------

def division_recipients(
    conn, division_id: int, team_ids: list[int] | None = None, parents: bool = True, coaches: bool = False,
) -> dict:
    """Who a message to this division reaches. With team_ids, only those
    teams' rostered players' parents and those teams' coaches; without, every
    player registered in or rostered in the division, and every team's coaches.

    Returns {"recipients": [{"email", "name", "role", "about"}], "missing":
    [str]} -- one recipient per address (a parent with two children here is
    listed once, "about" naming both), and "missing" naming everyone who was
    meant to be included but has no email on file."""
    by_email: dict[str, dict] = {}
    missing: list[str] = []

    def add(email: str | None, name: str, role: str, about: str) -> None:
        address = (email or "").strip().lower()
        if "@" not in address:
            # A parent is named by their child; a coach by their own name.
            missing.append(about if role == "parent" else f"{name} ({about})")
            return
        entry = by_email.setdefault(address, {"email": address, "name": name, "role": role, "about": []})
        if about not in entry["about"]:
            entry["about"].append(about)
        if role == "coach" and entry["role"] == "parent":
            entry["role"] = "parent and coach"

    team_filter = "AND t.id = ANY(%s)" if team_ids else ""
    team_params = (division_id, team_ids) if team_ids else (division_id,)

    if parents:
        rostered = conn.execute(
            f"""SELECT p.id, p.first_name, p.last_name, p.contact_first_name, p.contact_last_name,
                       p.contact_email, t.name
                FROM roster_entries re
                JOIN teams t ON t.id = re.team_id
                JOIN players p ON p.id = re.player_id
                WHERE t.division_id = %s AND t.deleted_at IS NULL AND p.deleted_at IS NULL {team_filter}
                ORDER BY p.last_name, p.first_name""",
            team_params,
        ).fetchall()
        seen = set()
        for player_id, first, last, c_first, c_last, email, team in rostered:
            seen.add(player_id)
            player = core.full_name(first, last)
            add(email, core.full_name(c_first, c_last) or f"Parent of {player}", "parent",
                f"{player} ({core.display_text(team)})")
        if not team_ids:
            registered = conn.execute(
                """SELECT p.id, p.first_name, p.last_name, p.contact_first_name, p.contact_last_name, p.contact_email
                   FROM players p JOIN player_divisions pd ON pd.player_id = p.id AND pd.division_id = %s
                   WHERE p.deleted_at IS NULL ORDER BY p.last_name, p.first_name""",
                (division_id,),
            ).fetchall()
            for player_id, first, last, c_first, c_last, email in registered:
                if player_id in seen:
                    continue
                player = core.full_name(first, last)
                add(email, core.full_name(c_first, c_last) or f"Parent of {player}", "parent", f"{player} (no team yet)")

    if coaches:
        rows = conn.execute(
            f"""SELECT c.first_name, c.last_name, c.email, t.name
                FROM team_coaches tc
                JOIN teams t ON t.id = tc.team_id
                JOIN coaches c ON c.id = tc.coach_id
                WHERE t.division_id = %s AND t.deleted_at IS NULL AND c.deleted_at IS NULL {team_filter}
                ORDER BY t.name, c.last_name, c.first_name""",
            team_params,
        ).fetchall()
        for first, last, email, team in rows:
            name = core.full_name(first, last)
            add(email, name, "coach", f"{core.display_text(team)} coach")

    recipients = sorted(by_email.values(), key=lambda r: (r["name"].lower(), r["email"]))
    for r in recipients:
        r["about"] = ", ".join(r["about"])
    return {"recipients": recipients, "missing": missing}


# ---------------------------------------------------------------------------
# Account emails: invitations and password resets
# ---------------------------------------------------------------------------

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_password_token(conn, user_id: int, purpose: str) -> str:
    """A one-time link token letting this user set their password: purpose
    "reset" (good for RESET_HOURS) or "invite" (INVITE_DAYS). Only its hash
    is stored. Any earlier unused token of theirs stops working."""
    if purpose not in ("reset", "invite"):
        raise ValueError("purpose must be 'reset' or 'invite'")
    raw = secrets.token_urlsafe(32)
    lifetime = f"{RESET_HOURS} hours" if purpose == "reset" else f"{INVITE_DAYS} days"
    conn.execute("UPDATE password_tokens SET used_at = now() WHERE user_id = %s AND used_at IS NULL", (user_id,))
    conn.execute(
        "INSERT INTO password_tokens (user_id, token_hash, purpose, expires_at) "
        "VALUES (%s, %s, %s, now() + %s::interval)",
        (user_id, _hash_token(raw), purpose, lifetime),
    )
    conn.commit()
    return raw


def recently_sent_token(conn, user_id: int, minutes: int = 2) -> bool:
    """Whether this user was sent a link in the last couple of minutes --
    so a reset form can't be used to flood someone's inbox."""
    row = conn.execute(
        "SELECT 1 FROM password_tokens WHERE user_id = %s AND created_at > now() - make_interval(mins => %s) LIMIT 1",
        (user_id, minutes),
    ).fetchone()
    return row is not None


def use_password_token(conn, raw_token: str, new_password: str) -> int:
    """Sets the password for whoever this token belongs to and retires the
    token. Raises ValueError if the link is unknown, used, or expired, if the
    account has been deactivated, or if the password is too short. Returns
    the user's id."""
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.")
    row = conn.execute(
        """SELECT pt.id, pt.user_id, u.deleted_at FROM password_tokens pt JOIN users u ON u.id = pt.user_id
           WHERE pt.token_hash = %s AND pt.used_at IS NULL AND pt.expires_at > now()""",
        (_hash_token(raw_token or ""),),
    ).fetchone()
    if row is None or row[2] is not None:
        raise ValueError("This link has expired or was already used. Ask for a new one.")
    core.set_user_password(conn, row[1], new_password)
    conn.execute("UPDATE password_tokens SET used_at = now() WHERE id = %s", (row[0],))
    conn.commit()
    return row[1]


def send_password_link(conn, user: dict, purpose: str, base_url: str, sent_by: int | None = None, post=None) -> dict:
    """Emails this user a link to set their password: an invitation to a new
    account, or a reset. `base_url` is the React app's address."""
    token = create_password_token(conn, user["id"], purpose)
    link = f"{base_url.rstrip('/')}/reset-password?token={token}"
    name = user.get("display_name") or "there"
    if purpose == "invite":
        subject = "Your Team Pittsburgh Team Manager account"
        text = (
            f"Hi {name},\n\nAn account has been set up for you on Team Pittsburgh Team Manager. "
            f"Choose your password here to sign in:\n\n{link}\n\n"
            f"The link works once and expires in {INVITE_DAYS} days. Your sign-in email is {user['email']}."
        )
    else:
        subject = "Reset your Team Pittsburgh Team Manager password"
        text = (
            f"Hi {name},\n\nSomeone asked to reset the password for this account. "
            f"Choose a new one here:\n\n{link}\n\n"
            f"The link works once and expires in {RESET_HOURS} hours. "
            "If it wasn't you, ignore this email and your password stays as it is."
        )
    return send(
        conn, to=[user["email"]], subject=subject, text=text,
        kind="invite" if purpose == "invite" else "password_reset", sent_by=sent_by, post=post,
    )


# ---------------------------------------------------------------------------
# Automatic notices
# ---------------------------------------------------------------------------

def notify_admins(conn, subject: str, text: str, post=None) -> dict | None:
    """Emails every active admin. Quietly does nothing (returns None) when
    email isn't configured or there's nobody to tell, since a notice is
    never worth failing the job that wanted to send it."""
    if not is_configured():
        return None
    admins = [u["email"] for u in core.list_users(conn) if u["is_admin"]]
    if not admins:
        return None
    try:
        return send(conn, to=admins, subject=subject, text=text, kind="notice", post=post)
    except MailError:
        return None


def league_sync_notice(reports: list[dict]) -> tuple[str, str] | None:
    """(subject, text) summarising a league-site sync for the admins, or None
    when there's nothing worth an email: no new games and nothing failed."""
    added = [(r["league"], game) for r in reports for game in r.get("added", [])]
    failed = [(r["league"], game) for r in reports for game in r.get("failed", [])]
    if not added and not failed:
        return None
    lines = []
    if added:
        lines.append(f"{len(added)} new game{'s' if len(added) != 1 else ''} came in from the league site:")
        lines += [f"  {league}: {game}" for league, game in added]
    if failed:
        lines.append("")
        lines.append(f"{len(failed)} game{'s' if len(failed) != 1 else ''} could not be saved and need a look:")
        lines += [f"  {league}: {game}" for league, game in failed]
    mismatches = [m for r in reports for m in r.get("number_mismatches", [])]
    if mismatches:
        lines.append("")
        lines.append("Jersey numbers that differ from the league site (not changed):")
        lines += [f"  {m}" for m in mismatches]
    link = app_url()
    if link:
        lines += ["", f"Standings and stats: {link}/stats-standings"]
    subject = (
        f"League sync: {len(failed)} game{'s' if len(failed) != 1 else ''} could not be saved" if failed
        else f"League sync: {len(added)} new game{'s' if len(added) != 1 else ''}"
    )
    return subject, "\n".join(lines)
