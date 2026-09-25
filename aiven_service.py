#!/usr/bin/env python3
"""
aiven_service.py

Minimal client for the Aiven Management API (https://api.aiven.io/v1),
used to check whether this app's Postgres service is powered on — and to
power it back on and wait for it to come up if not — before the app tries
to connect to it. Has no Streamlit dependency, same as game_sheet_core.py,
so it can also be used from a script or a health-check cron job.

Needs an Aiven API token (Profile -> API Tokens in the Aiven console, or
`avn user access-token create`), plus the project and service names, e.g.:

    AIVEN_API_TOKEN=...
    AIVEN_PROJECT_NAME=my-project
    AIVEN_SERVICE_NAME=pg-my-service

All three are optional from the app's point of view — if any is missing,
the app just skips this check and connects directly, same as before this
existed.
"""

import re
import time
from collections.abc import Callable

import requests

API_BASE = "https://api.aiven.io/v1"

# Reachable/healthy end state. Every other state (POWEROFF, REBUILDING,
# REBALANCING, MAINTENANCE, ...) means "not ready to accept connections yet".
RUNNING_STATE = "RUNNING"
POWERED_OFF_STATE = "POWEROFF"


class AivenServiceError(Exception):
    """Raised for any Aiven API failure (bad token, unknown service, no
    network, timeout, etc.) — callers only need to catch this one type,
    not know about `requests`."""


def _headers(api_token: str) -> dict:
    return {"Authorization": f"aivenv1 {api_token}"}


def _aiven_error_message(resp: requests.Response) -> str:
    """Aiven's error body (e.g. {"message": "...", "errors": [{"error_code":
    ..., "message": ...}]}) usually says exactly what's wrong — an IP
    allowlist block, a malformed token, an expired one, etc. — which is far
    more useful than collapsing every 401/403 into one generic guess.
    Falls back to the raw response text if it isn't JSON-shaped like that."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip() or f"HTTP {resp.status_code}"
    if isinstance(body, dict) and body.get("message"):
        return body["message"]
    return resp.text.strip() or f"HTTP {resp.status_code}"


def _call(method: str, path: str, api_token: str, timeout: float, **kwargs) -> dict:
    """Low-level call shared by every endpoint below — path is whatever
    comes after `/v1`, e.g. "/me" or "/project/x/service/y". Never logs or
    echoes api_token; only ever sends it in the Authorization header."""
    url = f"{API_BASE}{path}"
    try:
        resp = requests.request(method, url, headers=_headers(api_token), timeout=timeout, **kwargs)
    except requests.RequestException as e:
        raise AivenServiceError(f"Couldn't reach the Aiven API: {e}") from e
    if resp.status_code == 404:
        raise AivenServiceError(f"Aiven API: not found ({path}).")
    if resp.status_code in (401, 403):
        raise AivenServiceError(f"Aiven API rejected the request: {_aiven_error_message(resp)}")
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise AivenServiceError(f"Aiven API error ({resp.status_code}): {_aiven_error_message(resp)}") from e
    return resp.json()


def _service_call(method: str, api_token: str, project: str, service_name: str, timeout: float, **kwargs) -> dict:
    path = f"/project/{project}/service/{service_name}"
    try:
        return _call(method, path, api_token, timeout, **kwargs)["service"]
    except AivenServiceError as e:
        if "not found" in str(e):
            raise AivenServiceError(f"Service '{service_name}' not found in project '{project}'.") from e
        raise


def get_service_state(api_token: str, project: str, service_name: str, timeout: float = 10) -> str:
    """The service's current state, e.g. "RUNNING", "POWEROFF", "REBUILDING"."""
    return _service_call("GET", api_token, project, service_name, timeout)["state"]


def power_on_service(api_token: str, project: str, service_name: str, timeout: float = 10) -> str:
    """Ask Aiven to power the service back on. Returns whatever state Aiven
    reports immediately after the request — usually not RUNNING yet; poll
    with wait_until_running() for that."""
    return _service_call("PUT", api_token, project, service_name, timeout, json={"powered": True})["state"]


def wait_until_running(
    api_token: str,
    project: str,
    service_name: str,
    poll_interval: float = 5,
    max_wait: float = 300,
    on_poll: Callable[[str, float], None] | None = None,
) -> str:
    """Poll the service's state until it's RUNNING or max_wait seconds have
    passed. Calls on_poll(state, elapsed_seconds) after every check, if
    given, so a caller (e.g. Streamlit) can show live progress. Raises
    AivenServiceError on timeout."""
    start = time.monotonic()
    while True:
        state = get_service_state(api_token, project, service_name, timeout=max(10, poll_interval))
        elapsed = time.monotonic() - start
        if on_poll:
            on_poll(state, elapsed)
        if state == RUNNING_STATE:
            return state
        if elapsed >= max_wait:
            raise AivenServiceError(
                f"Timed out after {int(elapsed)}s waiting for '{service_name}' to reach "
                f"{RUNNING_STATE} (currently '{state}')."
            )
        time.sleep(poll_interval)


def ensure_service_running(
    api_token: str,
    project: str,
    service_name: str,
    poll_interval: float = 5,
    max_wait: float = 300,
    on_poll: Callable[[str, float], None] | None = None,
) -> str:
    """Check the service's state; power it on and wait for RUNNING if it's
    currently powered off. A no-op beyond the initial check if it's already
    running. If it's in some other transitional state (REBUILDING,
    REBALANCING, MAINTENANCE, ...) — already on its way, or busy for a
    reason unrelated to being powered off — this waits for it rather than
    issuing a redundant power-on."""
    state = get_service_state(api_token, project, service_name)
    if state == RUNNING_STATE:
        return state
    if state == POWERED_OFF_STATE:
        power_on_service(api_token, project, service_name)
    return wait_until_running(
        api_token, project, service_name, poll_interval=poll_interval, max_wait=max_wait, on_poll=on_poll
    )


# ---------------------------------------------------------------------------
# API token management — validating the configured token and rotating it.
# These hit account-level endpoints (/me, /access_token), not a specific
# project/service, and are meant for a human-run CLI flow (see
# scripts/aiven_token.py), not for the app to call on its own — creating or
# revoking a token is a real, account-wide, hard-to-reverse action that
# deserves a person deciding to do it, not a background check doing it
# silently.
# ---------------------------------------------------------------------------

def get_current_user(api_token: str, timeout: float = 10) -> dict:
    """Whoever this token authenticates as. The cheapest possible "is this
    token still valid" check — a 401/403 raises AivenServiceError (caught
    and reported as invalid), a 200 means it's good."""
    return _call("GET", "/me", api_token, timeout)["user"]


def list_access_tokens(api_token: str, timeout: float = 10) -> list[dict]:
    """Metadata (description, expiry_time, last_used_time, token_prefix,
    ...) for every access token on this account. Never includes the secret
    itself — Aiven only ever returns that once, at creation."""
    return _call("GET", "/access_token", api_token, timeout)["tokens"]


def find_token_info(api_token: str, timeout: float = 10) -> dict | None:
    """Which entry in list_access_tokens() this token actually is, found by
    matching its prefix — so a caller can report *this* token's expiry and
    last-used time, not just "some token on the account works". Returns
    None if no listed entry's prefix matches (token type list can't see,
    just revoked, etc.)."""
    for entry in list_access_tokens(api_token, timeout=timeout):
        prefix = entry.get("token_prefix")
        if prefix and api_token.startswith(prefix):
            return entry
    return None


def create_access_token(
    api_token: str,
    description: str,
    max_age_seconds: int | None = None,
    extend_when_used: bool = False,
    timeout: float = 10,
) -> dict:
    """Create a brand-new access token on the account api_token
    authenticates as. The returned dict's "full_token" is the new secret —
    Aiven shows it exactly once, right here; save it immediately (e.g. into
    .env), since it can't be retrieved again later, only revoked and
    replaced. The token used to make this call is untouched and keeps
    working until separately revoked with revoke_access_token() — rotating
    is create-new-then-revoke-old, two explicit steps, not one."""
    body = {"description": description, "extend_when_used": extend_when_used, "max_age_seconds": max_age_seconds}
    return _call("POST", "/access_token", api_token, timeout, json=body)


def revoke_access_token(api_token: str, token_prefix: str, timeout: float = 10) -> None:
    """Permanently revoke a token, identified by its prefix (or the full
    token string). Irreversible. If the revoked token is the one anything
    is currently using to authenticate — including this app, if it's
    running — that stops working immediately."""
    _call("DELETE", f"/access_token/{token_prefix}", api_token, timeout)


# ---------------------------------------------------------------------------
# Service network access (ip_filter) — which client IPs may even reach the
# database port at all. Separate from the access-token IP allowlist above
# (that one gates the *Management API*; this one gates the database
# itself). A freshly-created Aiven service defaults this wide open
# (["0.0.0.0/0", "::/0"]) — anyone, from anywhere, can attempt to connect
# (though they'd still need valid database credentials).
# ---------------------------------------------------------------------------

STREAMLIT_CLOUD_STATUS_URL = "https://docs.streamlit.io/deploy/streamlit-community-cloud/status"
# The page's actual DOM order is: intro text ("...following IP
# addresses:") -> a "Warning" callout ("...may change at any time without
# notice.") -> then a separate <section> holding the real IP list. So the
# IPs are scraped from the first <section>...</section> block found *after*
# the warning text, not from between the two anchor phrases (that would
# grab the warning callout's own markup and nothing else). There's no API
# for this list — Streamlit's docs say so explicitly — and that same
# warning is the whole reason this needs checking periodically rather than
# hardcoded once.
_STREAMLIT_INTRO_MARKER = "following IP addresses:"
_STREAMLIT_WARNING_MARKER = "may change at any time"
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def get_streamlit_community_cloud_ips(timeout: float = 10) -> list[str]:
    """Scrape Streamlit Community Cloud's published outbound IPs, as CIDR
    /32 entries ready to drop into an Aiven ip_filter. Raises
    AivenServiceError if the page can't be fetched or no longer matches the
    expected shape, rather than silently returning an empty or partial
    list — a security-relevant list should fail loudly, not quietly go
    stale or empty."""
    try:
        resp = requests.get(STREAMLIT_CLOUD_STATUS_URL, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        raise AivenServiceError(f"Couldn't fetch Streamlit's IP list: {e}") from e

    text = resp.text
    intro_idx = text.find(_STREAMLIT_INTRO_MARKER)
    warning_idx = text.find(_STREAMLIT_WARNING_MARKER, intro_idx if intro_idx != -1 else 0)
    if intro_idx == -1 or warning_idx == -1:
        raise AivenServiceError(
            "Streamlit's status page doesn't match the expected format anymore — can't safely "
            f"extract the IP list. Check {STREAMLIT_CLOUD_STATUS_URL} manually."
        )
    section_start = text.find("<section", warning_idx)
    section_end = text.find("</section>", section_start) if section_start != -1 else -1
    if section_start == -1 or section_end == -1:
        raise AivenServiceError(
            "Streamlit's status page doesn't match the expected format anymore — found the IP-list "
            f"intro text but not the list itself. Check {STREAMLIT_CLOUD_STATUS_URL} manually."
        )
    ips = sorted(
        set(_IPV4_RE.findall(text[section_start:section_end])), key=lambda ip: tuple(int(p) for p in ip.split("."))
    )
    if not ips:
        raise AivenServiceError("Found the IP-list section on Streamlit's status page, but no IPs inside it.")
    return [f"{ip}/32" for ip in ips]


def get_service_user_config(api_token: str, project: str, service_name: str, timeout: float = 10) -> dict:
    """The service's full current user_config (backup schedule, Postgres
    version, ip_filter, and every other tunable) — needed as a base for
    set_service_ip_filter() so that update doesn't disturb anything else."""
    return _service_call("GET", api_token, project, service_name, timeout).get("user_config", {})


def get_service_ip_filter(api_token: str, project: str, service_name: str, timeout: float = 10) -> list[str]:
    """The service's current ip_filter (a list of CIDR strings) — which
    client IPs may reach it at all."""
    return get_service_user_config(api_token, project, service_name, timeout).get("ip_filter", [])


def set_service_ip_filter(
    api_token: str, project: str, service_name: str, cidrs: list[str], timeout: float = 10
) -> list[str]:
    """Replace the service's ip_filter. Fetches the current full
    user_config first and sends it back with only "ip_filter" changed,
    rather than sending {"ip_filter": cidrs} alone — this service also has
    backup schedule, Postgres version, and other settings under
    user_config, and an update meant only to touch network access
    shouldn't risk resetting those to defaults if a bare partial update
    isn't actually merged server-side."""
    current = get_service_user_config(api_token, project, service_name, timeout)
    updated = dict(current)
    updated["ip_filter"] = cidrs
    result = _service_call("PUT", api_token, project, service_name, timeout, json={"user_config": updated})
    return result.get("user_config", {}).get("ip_filter", [])


def diff_ip_filter_against_streamlit(
    api_token: str,
    project: str,
    service_name: str,
    extra_cidrs: list[str] | None = None,
    timeout: float = 10,
) -> dict:
    """Compare the service's current ip_filter against Streamlit Community
    Cloud's published IPs, plus any extra_cidrs a caller wants preserved
    (e.g. an admin's own IP for direct/CLI access). Returns {"missing":
    [...], "extra": [...], "in_sync": bool}:
      - "missing": Streamlit (or extra_cidrs) entries not currently
        allowed — the real risk, since it means the app may not be able to
        reach its own database.
      - "extra": currently-allowed entries that are neither a Streamlit IP
        nor one of extra_cidrs. Not necessarily wrong (could be
        intentional), just flagged for a human to judge — e.g. a leftover
        "0.0.0.0/0" would show up here."""
    streamlit_ips = set(get_streamlit_community_cloud_ips(timeout=timeout))
    wanted = streamlit_ips | set(extra_cidrs or [])
    current = set(get_service_ip_filter(api_token, project, service_name, timeout=timeout))
    missing = sorted(wanted - current)
    extra = sorted(current - wanted)
    return {"missing": missing, "extra": extra, "in_sync": not missing and not extra}
