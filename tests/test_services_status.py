import league_site_sync
import services_status as s

RENDER = s.PROVIDERS[0]

SUMMARY = {
    "status": {"indicator": "none", "description": "All Systems Operational"},
    "components": [
        {"name": "Web Services", "status": "operational"},
        {"name": "Oregon", "status": "partial_outage"},
        {"name": "A group heading", "status": "partial_outage", "group": True},
    ],
    "incidents": [],
    "scheduled_maintenances": [{
        "name": "Render Maintenance Period", "status": "scheduled", "impact": "maintenance",
        "shortlink": "https://stspg.io/x", "scheduled_for": "2026-10-14T01:00:00.000Z",
        "scheduled_until": "2026-10-14T02:00:00.000Z",
        "incident_updates": [{"body": "We will be upgrading critical infrastructure."}],
    }],
}


def test_parse_summary_keeps_notices_and_degraded_parts():
    parsed = s.parse_summary(RENDER, SUMMARY)
    assert (parsed["name"], parsed["indicator"], parsed["description"]) == ("Render", "none", "All Systems Operational")
    assert parsed["incidents"] == []
    [maintenance] = parsed["maintenances"]
    assert maintenance["name"] == "Render Maintenance Period"
    assert maintenance["scheduled_for"] == "2026-10-14T01:00:00.000Z"
    assert maintenance["body"] == "We will be upgrading critical infrastructure."
    assert maintenance["url"] == "https://stspg.io/x"
    assert parsed["degraded"] == ["Oregon: partial outage"]


def test_a_status_page_that_cannot_be_read_is_unknown_not_an_error():
    def down(url, headers=None, timeout=8):
        raise OSError("timed out")

    status = s.provider_status(RENDER, get_json=down)
    assert status["indicator"] == "unknown" and "timed out" in status["description"]
    assert status["incidents"] == [] and status["maintenances"] == []
    assert s.check_league_site(get_json=down)["state"] == "problem"


def test_own_checks(conn, monkeypatch):
    assert s.check_database(conn)["state"] == "ok"

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert s.check_email()["state"] == "warning"
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("EMAIL_FROM", "Team Pittsburgh <noreply@tptm.io>")
    domains = lambda state: (lambda url, headers=None, timeout=8: {"data": [{"name": "tptm.io", "status": state}]})  # noqa: E731
    assert s.check_email(get_json=domains("verified"))["state"] == "ok"
    pending = s.check_email(get_json=domains("pending"))
    assert pending["state"] == "problem" and "pending" in pending["detail"]

    def github(conclusion):
        return lambda url, headers=None, timeout=8: {"workflow_runs": [{"conclusion": conclusion}]}

    # Never run yet; then just run; then two turns overdue; then off.
    assert s.check_sync(conn, get_json=github("success"))["state"] == "warning"
    league_site_sync.mark_scheduled_run(conn)
    assert s.check_sync(conn, get_json=github("success"))["state"] == "ok"
    assert s.check_sync(conn, get_json=github("failure"))["state"] == "problem"
    conn.execute("UPDATE app_settings SET value = (now() - interval '5 hours')::text WHERE key = 'league_sync_last_run'")
    overdue = s.check_sync(conn, get_json=github("success"))
    assert overdue["state"] == "problem" and "5.0 hours ago" in overdue["detail"]
    league_site_sync.set_schedule(conn, 0)
    assert s.check_sync(conn, get_json=github("success"))["state"] == "warning"
