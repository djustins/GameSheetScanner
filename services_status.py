"""
services_status.py

One look at everything this app depends on, for the admins' Services page:

  - Each outside service's own published status -- current incidents and
    upcoming maintenance -- read from its public status page. All six
    publish the same Statuspage-style summary feed (/api/v2/summary.json).
  - This app's own checks: can it reach its database, is email set up, is
    the league sync running, is the league stats site answering.

Nothing here needs an account with any of the services except the two that
are already configured (the database connection and, for the sending
domain's state, the Resend key). A check that can't be made reports itself
as "unknown" rather than failing the page.
"""

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import league_site_sync
import mailer

GITHUB_REPO = "djustins/GameSheetScanner"
SYNC_WORKFLOW = "nightly-league-sync.yml"

# What each service does for this app, where its status feed is, and where
# an admin goes to act on it.
PROVIDERS = [
    {"key": "render", "name": "Render", "role": "Runs the API, which everything in the app goes through.",
     "status_url": "https://status.render.com", "dashboard_url": "https://dashboard.render.com/"},
    {"key": "vercel", "name": "Vercel", "role": "Hosts the app itself at tptm.io.",
     "status_url": "https://www.vercel-status.com", "dashboard_url": "https://vercel.com/dashboard"},
    {"key": "aiven", "name": "Aiven", "role": "Hosts the database: every player, game and roster.",
     "status_url": "https://status.aiven.io", "dashboard_url": "https://console.aiven.io/"},
    {"key": "github", "name": "GitHub", "role": "Holds the code and runs the hourly league sync job.",
     "status_url": "https://www.githubstatus.com", "dashboard_url": f"https://github.com/{GITHUB_REPO}/actions"},
    {"key": "resend", "name": "Resend", "role": "Sends the app's email.",
     "status_url": "https://resend-status.com", "dashboard_url": "https://resend.com/emails"},
    {"key": "anthropic", "name": "Claude (Anthropic)", "role": "Reads scanned game sheets.",
     "status_url": "https://status.claude.com", "dashboard_url": "https://console.anthropic.com/"},
]

CACHE_SECONDS = 120
_cache: dict = {"at": 0.0, "data": None}


def _get_json(url: str, headers: dict | None = None, timeout: int = 8):
    request = urllib.request.Request(url, headers={"User-Agent": "team-pittsburgh-team-manager/1.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _notice(item: dict) -> dict:
    """One incident or maintenance from a status feed, trimmed to what the page shows."""
    updates = item.get("incident_updates") or []
    return {
        "name": item.get("name"),
        "status": item.get("status"),
        "impact": item.get("impact"),
        "url": item.get("shortlink") or item.get("url"),
        "updated_at": item.get("updated_at"),
        "scheduled_for": item.get("scheduled_for"),
        "scheduled_until": item.get("scheduled_until"),
        # The newest update's text: what the service last said about it.
        "body": (updates[0].get("body") if updates else None) or item.get("body"),
    }


def parse_summary(provider: dict, summary: dict) -> dict:
    """A status feed's summary as this app reports it. `indicator` is the
    feed's own: "none" (all fine), "minor", "major", "critical" or
    "maintenance"."""
    status = summary.get("status") or {}
    return {
        **provider,
        "indicator": status.get("indicator") or "unknown",
        "description": status.get("description") or "Status unavailable",
        "incidents": [_notice(i) for i in summary.get("incidents") or []],
        "maintenances": [_notice(m) for m in summary.get("scheduled_maintenances") or []],
        # Parts of the service that aren't fully working, by name.
        "degraded": [
            f"{c['name']}: {str(c.get('status', '')).replace('_', ' ')}"
            for c in summary.get("components") or []
            if c.get("status") not in (None, "operational") and not c.get("group")
        ][:8],
    }


def provider_status(provider: dict, get_json=_get_json) -> dict:
    try:
        return parse_summary(provider, get_json(provider["status_url"] + "/api/v2/summary.json"))
    except (OSError, ValueError) as e:
        return {
            **provider, "indicator": "unknown", "description": f"Couldn't read its status page ({e})",
            "incidents": [], "maintenances": [], "degraded": [],
        }


# ---------------------------------------------------------------------------
# This app's own checks
# ---------------------------------------------------------------------------

def _check(name: str, state: str, detail: str) -> dict:
    """state: "ok", "warning", "problem" or "unknown"."""
    return {"name": name, "state": state, "detail": detail}


def check_database(conn) -> dict:
    try:
        started = time.monotonic()
        games = conn.execute("SELECT count(*) FROM games").fetchone()[0]
        return _check("Database", "ok", f"Answering in {int((time.monotonic() - started) * 1000)} ms; {games} games stored.")
    except Exception as e:  # the one check whose failure can be anything a driver raises
        return _check("Database", "problem", f"Not answering: {e}")


def check_email(get_json=_get_json) -> dict:
    if not mailer.is_configured():
        return _check("Email", "warning", "Not set up on the server: RESEND_API_KEY and EMAIL_FROM are needed. Nothing can be sent.")
    sender = mailer.from_address() or ""
    domain = sender.split("@")[-1].strip("> ").lower()
    try:
        domains = get_json("https://api.resend.com/domains",
                           {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"}).get("data") or []
    except (OSError, ValueError):
        return _check("Email", "ok", f"Set up, sending from {sender}. Couldn't confirm the domain's state with Resend just now.")
    state = next((d.get("status") for d in domains if (d.get("name") or "").lower() == domain), None)
    if state == "verified":
        return _check("Email", "ok", f"Set up, sending from {sender}. The domain {domain} is verified.")
    return _check("Email", "problem", f"Sending from {sender}, but Resend shows {domain} as {state or 'not added'}: email will be refused until it's verified.")


def check_scanner() -> dict:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _check("Game sheet scanner", "ok", "The key for reading scanned sheets is set.")
    return _check("Game sheet scanner", "warning", "ANTHROPIC_API_KEY isn't set on the server: Import Scoresheets can't read a sheet.")


def check_league_site(get_json=_get_json) -> dict:
    try:
        leagues = get_json(league_site_sync.API_BASE + "admin/leagues/current").get("currentLeagues") or []
        return _check("League stats site", "ok", f"Answering, with {len(leagues)} current leagues.")
    except (OSError, ValueError) as e:
        return _check("League stats site", "problem", f"Not answering, so the sync can't bring in games: {e}")


def check_sync(conn, get_json=_get_json) -> dict:
    """The automatic league sync: its schedule, when it last ran, and
    whether GitHub's last run of the job succeeded."""
    schedule = league_site_sync.get_schedule(conn)
    if schedule["every_hours"] == 0:
        return _check("League sync", "warning", "Automatic syncing is turned off (My Account → League site sync).")
    often = "once a day" if schedule["every_hours"] == 24 else (
        "every hour" if schedule["every_hours"] == 1 else f"every {schedule['every_hours']} hours")
    hours_since = None
    if schedule["last_run"]:
        hours_since = float(conn.execute(
            "SELECT EXTRACT(EPOCH FROM now() - %s::timestamptz) / 3600", (schedule["last_run"],)
        ).fetchone()[0])
    job = ""
    try:
        runs = get_json(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{SYNC_WORKFLOW}/runs?per_page=1"
        ).get("workflow_runs") or []
        if runs and runs[0].get("conclusion") == "failure":
            return _check("League sync", "problem", f"Set to {often}, but GitHub's last run of the job failed. Open GitHub Actions to see why.")
        if runs:
            job = " GitHub's last run of the job succeeded." if runs[0].get("conclusion") == "success" else ""
    except (OSError, ValueError):
        pass
    if hours_since is None:
        return _check("League sync", "warning", f"Set to {often}, but no automatic sync has run yet.{job}")
    ago = f"{int(hours_since * 60)} minutes ago" if hours_since < 1 else f"{hours_since:.1f} hours ago"
    # Two missed turns in a row means something is stopping it.
    if hours_since > schedule["every_hours"] * 2 + 1:
        return _check("League sync", "problem", f"Set to {often}, but the last automatic sync was {ago}.{job}")
    return _check("League sync", "ok", f"Set to {often}; last ran {ago}.{job}")


def snapshot(conn, refresh: bool = False) -> dict:
    """Everything the Services page shows. The outside lookups are made
    side by side and kept for CACHE_SECONDS, so opening the page twice
    doesn't ask every status page twice; refresh=True asks again now."""
    if not refresh and _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        providers = [pool.submit(provider_status, p) for p in PROVIDERS]
        email = pool.submit(check_email)
        league = pool.submit(check_league_site)
        # The checks that use the database connection stay on this thread.
        checks = [check_database(conn), check_sync(conn), email.result(), league.result(), check_scanner()]
        data = {
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "providers": [f.result() for f in providers],
            "checks": checks,
        }
    _cache.update(at=time.time(), data=data)
    return data
