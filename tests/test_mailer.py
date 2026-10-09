import os

import pytest

# As in test_api.py: api.py reads DATABASE_URL when it's imported, so it has to
# be the test database's before that import -- otherwise these requests would
# go to the real one.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB_URL:
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)
os.environ["DATABASE_URL"] = TEST_DB_URL

import api  # noqa: E402
import game_sheet_core as core  # noqa: E402
import mailer  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

assert api.DATABASE_URL == TEST_DB_URL, "api.py was imported before DATABASE_URL pointed at the test database"

client = TestClient(api.app)
ADMIN = ("mail-admin@example.com", "correct-horse-battery")


@pytest.fixture
def outbox(monkeypatch):
    """Email configured, with Resend replaced by a list that collects what would be sent."""
    sent = []
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM", "Team Pittsburgh <noreply@tptm.io>")
    monkeypatch.setenv("APP_URL", "https://app.tptm.io")
    monkeypatch.setattr(mailer, "_post_batch", lambda messages: sent.extend(messages))
    return sent


@pytest.fixture
def admin(conn):
    return core.add_user(conn, ADMIN[0], ADMIN[1], display_name="Mail Admin", is_admin=True)


def _division(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    crosby = core.add_player(
        conn, "Sidney", "Crosby", current_division_id=division_id,
        contact_first_name="Trina", contact_last_name="Crosby", contact_email="Trina@Example.com",
    )
    taylor = core.add_player(conn, "Taylor", "Crosby", current_division_id=division_id, contact_email="trina@example.com")
    orr = core.add_player(conn, "Bobby", "Orr", current_division_id=division_id)  # no email on file
    unplaced = core.add_player(conn, "Mario", "Lemieux", current_division_id=division_id, contact_email="pierrette@example.com")
    core.add_roster_entry(conn, avs, "87", "Sidney Crosby", player_id=crosby)
    core.add_roster_entry(conn, wild, "8", "Taylor Crosby", player_id=taylor)
    core.add_roster_entry(conn, wild, "4", "Bobby Orr", player_id=orr)
    coach = core.add_coach(conn, "Pat", "Coach", email="pat@example.com")
    core.assign_coach_to_team(conn, wild, coach)
    return avs, wild, unplaced


def test_text_to_html_escapes_and_links():
    out = mailer.text_to_html("Hi <all>,\nline two\n\nSee https://tptm.io/x now")
    assert "<p>Hi &lt;all&gt;,<br>line two</p>" in out
    assert '<a href="https://tptm.io/x">https://tptm.io/x</a>' in out


def test_division_recipients_dedupes_parents_and_reports_missing(conn, division_id):
    avs, wild, _ = _division(conn, division_id)

    everyone = mailer.division_recipients(conn, division_id, parents=True, coaches=True)
    by_email = {r["email"]: r for r in everyone["recipients"]}
    assert set(by_email) == {"trina@example.com", "pierrette@example.com", "pat@example.com"}
    # One email for a parent of two, naming both children.
    assert by_email["trina@example.com"]["about"] == "Sidney Crosby (Avalanche), Taylor Crosby (Wild)"
    assert by_email["pierrette@example.com"]["about"] == "Mario Lemieux (no team yet)"
    assert by_email["pat@example.com"]["role"] == "coach"
    assert everyone["missing"] == ["Bobby Orr (Wild)"]

    # One team only: its rostered players' parents, not the unplaced player's.
    one_team = mailer.division_recipients(conn, division_id, [avs], parents=True, coaches=True)
    assert [r["email"] for r in one_team["recipients"]] == ["trina@example.com"]
    assert one_team["recipients"][0]["about"] == "Sidney Crosby (Avalanche)"
    coaches_only = mailer.division_recipients(conn, division_id, [wild], parents=False, coaches=True)
    assert [r["email"] for r in coaches_only["recipients"]] == ["pat@example.com"]


def test_send_message_over_the_api_one_email_each_and_logged(conn, division_id, admin, outbox):
    _division(conn, division_id)
    body = {"division_id": division_id, "parents": True, "coaches": True, "subject": "Practice moved", "body": "See you at 6."}

    preview = client.post("/email/recipients", json=body, auth=ADMIN).json()
    assert len(preview["recipients"]) == 3 and len(preview["missing"]) == 1

    test = client.post("/email/send", json={**body, "test": True}, auth=ADMIN)
    assert test.json() == {"sent": 1, "failed": 0, "error": None}
    assert [m["to"] for m in outbox] == [[ADMIN[0]]] and outbox[0]["subject"] == "[Test] Practice moved"

    outbox.clear()
    sent = client.post("/email/send", json=body, auth=ADMIN)
    assert sent.json()["sent"] == 3
    assert sorted(m["to"][0] for m in outbox) == ["pat@example.com", "pierrette@example.com", "trina@example.com"]
    assert all(len(m["to"]) == 1 and m["reply_to"] == ADMIN[0] and m["from"].endswith("<noreply@tptm.io>") for m in outbox)

    log = client.get("/email/log", auth=ADMIN).json()
    assert [(row["kind"], row["sent"]) for row in log] == [("message", 3), ("test", 1)]
    assert log[0]["audience"] == "parents and coaches — whole division" and log[0]["sent_by"] == "Mail Admin"

    assert client.post("/email/send", json={**body, "subject": " "}, auth=ADMIN).status_code == 422


def test_email_is_admin_only_and_reports_when_not_configured(conn, division_id, admin, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    _division(conn, division_id)
    role_id = core.add_role(conn, "Coach", pages=list(core.PAGES))
    core.add_user(conn, "coach@example.com", "pw-pw-pw-pw", role_id=role_id)
    body = {"division_id": division_id, "subject": "Hi", "body": "Hi"}
    assert client.post("/email/send", json=body, auth=("coach@example.com", "pw-pw-pw-pw")).status_code == 403
    assert client.get("/email/status", auth=ADMIN).json() == {"configured": False, "from": None}
    assert client.post("/email/send", json=body, auth=ADMIN).status_code == 503


def test_password_reset_link_works_once_and_reveals_nothing(conn, admin, outbox):
    # An unknown address gets the same answer and no email.
    assert client.post("/password-reset/request", json={"email": "nobody@example.com"}).status_code == 204
    assert outbox == []

    assert client.post("/password-reset/request", json={"email": ADMIN[0].upper()}).status_code == 204
    assert len(outbox) == 1 and outbox[0]["to"] == [ADMIN[0]]
    link = next(word for word in outbox[0]["text"].split() if "reset-password?token=" in word)
    assert link.startswith("https://app.tptm.io/reset-password?token=")
    token = link.split("token=")[1]

    # Asking again straight away doesn't send a second one.
    client.post("/password-reset/request", json={"email": ADMIN[0]})
    assert len(outbox) == 1

    assert client.post("/password-reset/confirm", json={"token": token, "password": "short"}).status_code == 400
    assert client.post("/password-reset/confirm", json={"token": token, "password": "a-new-password"}).status_code == 204
    assert core.verify_login(conn, ADMIN[0], "a-new-password") is not None
    assert core.verify_login(conn, ADMIN[0], ADMIN[1]) is None
    # Used once: the same link is dead.
    assert client.post("/password-reset/confirm", json={"token": token, "password": "another-password"}).status_code == 400
    # The log never exposes the link.
    assert mailer.list_email_log(conn)[0]["body"] is None


def test_invite_emails_a_set_password_link(conn, admin, outbox):
    invited = core.add_user(conn, "new-coach@example.com", "temporary-password", display_name="New Coach")
    response = client.post(f"/users/{invited}/invite", auth=(ADMIN[0], ADMIN[1]))
    assert response.status_code == 200 and response.json() == {"sent_to": "new-coach@example.com"}
    assert "Hi New Coach" in outbox[0]["text"] and "expires in 7 days" in outbox[0]["text"]
    token = outbox[0]["text"].split("token=")[1].split()[0]
    mailer.use_password_token(conn, token, "chosen-by-coach")
    assert core.verify_login(conn, "new-coach@example.com", "chosen-by-coach") is not None


def test_league_sync_notice_only_when_something_happened(conn, admin, outbox):
    quiet = [{"league": "Penguin - Fall - 2026", "added": [], "failed": [], "number_mismatches": []}]
    assert mailer.league_sync_notice(quiet) is None

    busy = [{"league": "Penguin - Fall - 2026", "added": ["2026-10-08 Admirals 3-2 Mariners"],
             "failed": ["2026-10-08 Wolves 2-2 Barons: Games can't end in a tie"], "number_mismatches": []}]
    subject, text = mailer.league_sync_notice(busy)
    assert subject == "League sync: 1 game could not be saved" and "Admirals 3-2 Mariners" in text
    mailer.notify_admins(conn, subject, text)
    assert outbox[-1]["to"] == [ADMIN[0]] and "could not be saved" in outbox[-1]["text"]
