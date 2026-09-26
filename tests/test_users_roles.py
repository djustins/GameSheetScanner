import game_sheet_core as core


def test_add_user_and_verify_login(conn):
    core.add_user(conn, "coach@example.com", "correct-horse", display_name="Coach")
    user = core.verify_login(conn, "coach@example.com", "correct-horse")
    assert user is not None
    assert user["email"] == "coach@example.com"
    assert "password_hash" not in user


def test_verify_login_rejects_wrong_password(conn):
    core.add_user(conn, "coach@example.com", "correct-horse")
    assert core.verify_login(conn, "coach@example.com", "wrong-password") is None


def test_verify_login_rejects_unknown_email(conn):
    assert core.verify_login(conn, "nobody@example.com", "anything") is None


def test_verify_login_rejects_deactivated_user(conn):
    user_id = core.add_user(conn, "coach@example.com", "correct-horse")
    core.soft_delete_user(conn, user_id)
    assert core.verify_login(conn, "coach@example.com", "correct-horse") is None

    core.restore_user(conn, user_id)
    assert core.verify_login(conn, "coach@example.com", "correct-horse") is not None


def test_email_is_case_and_whitespace_insensitive(conn):
    core.add_user(conn, "  Coach@Example.com  ", "correct-horse")
    assert core.verify_login(conn, "coach@example.com", "correct-horse") is not None


def test_role_grants_pages_and_flags(conn):
    role_id = core.add_role(
        conn, "Coach", pages=["edit", "standings", "stats"], read_only=True, hide_contact_details=True
    )
    user_id = core.add_user(conn, "coach@example.com", "correct-horse", role_id=role_id)
    user = core.verify_login(conn, "coach@example.com", "correct-horse")
    assert set(user["pages"]) == {"edit", "standings", "stats"}
    assert user["read_only"] is True
    assert user["hide_contact_details"] is True
    assert user["is_admin"] is False
    assert user_id == user["id"]


def test_user_with_no_role_has_no_pages(conn):
    core.add_user(conn, "nobody@example.com", "correct-horse")
    user = core.verify_login(conn, "nobody@example.com", "correct-horse")
    assert user["pages"] == []
    assert user["read_only"] is False


def test_admin_flag_is_independent_of_role_pages(conn):
    core.add_user(conn, "admin@example.com", "correct-horse", is_admin=True)
    user = core.verify_login(conn, "admin@example.com", "correct-horse")
    assert user["is_admin"] is True
    # Admins bypass per-page checks in the app itself (visible_pages = all
    # of core.PAGES when is_admin) -- verify_login itself only reports
    # what the *role* grants, since that bypass lives in app.py.
    assert user["pages"] == []


def test_update_role_pages(conn):
    role_id = core.add_role(conn, "Coach", pages=["edit"])
    core.update_role(conn, role_id, pages=["edit", "standings"])
    roles = core.list_roles(conn)
    role = next(r for r in roles if r["id"] == role_id)
    assert set(role["pages"]) == {"edit", "standings"}


def test_deleting_a_role_clears_it_from_its_users(conn):
    role_id = core.add_role(conn, "Coach", pages=["edit"])
    user_id = core.add_user(conn, "coach@example.com", "correct-horse", role_id=role_id)
    core.delete_role(conn, role_id)
    users = core.list_users(conn)
    user = next(u for u in users if u["id"] == user_id)
    assert user["role_id"] is None
    assert user["pages"] == []


def test_set_user_role_reassigns_pages(conn):
    role1 = core.add_role(conn, "Coach", pages=["edit"])
    role2 = core.add_role(conn, "Scorer", pages=["standings", "stats"])
    user_id = core.add_user(conn, "u@example.com", "correct-horse", role_id=role1)
    core.set_user_role(conn, user_id, role2)
    user = core.verify_login(conn, "u@example.com", "correct-horse")
    assert set(user["pages"]) == {"standings", "stats"}


def test_all_page_keys_have_a_label():
    # core.PAGES is validated against by scripts/manage_users.py and shown
    # in User Management -- every key needs a human-readable label.
    assert all(isinstance(label, str) and label for label in core.PAGES.values())


def test_create_and_verify_api_token(conn):
    user_id = core.add_user(conn, "coach@example.com", "correct-horse", is_admin=True)
    token_id, raw_token = core.create_api_token(conn, user_id, "My Laptop")
    assert raw_token.startswith("gst_")

    verified = core.verify_api_token(conn, raw_token)
    assert verified is not None
    assert verified["id"] == user_id
    assert verified["is_admin"] is True
    assert "password_hash" not in verified


def test_verify_api_token_rejects_garbage(conn):
    assert core.verify_api_token(conn, "not-a-real-token") is None


def test_verify_api_token_rejects_revoked_token(conn):
    user_id = core.add_user(conn, "coach@example.com", "correct-horse")
    token_id, raw_token = core.create_api_token(conn, user_id, "My Laptop")
    core.revoke_api_token(conn, token_id, user_id)
    assert core.verify_api_token(conn, raw_token) is None


def test_verify_api_token_rejects_deactivated_user(conn):
    user_id = core.add_user(conn, "coach@example.com", "correct-horse")
    token_id, raw_token = core.create_api_token(conn, user_id, "My Laptop")
    core.soft_delete_user(conn, user_id)
    assert core.verify_api_token(conn, raw_token) is None


def test_list_api_tokens_never_exposes_the_raw_token_or_hash(conn):
    user_id = core.add_user(conn, "coach@example.com", "correct-horse")
    core.create_api_token(conn, user_id, "My Laptop")
    tokens = core.list_api_tokens(conn, user_id)
    assert len(tokens) == 1
    assert tokens[0]["name"] == "My Laptop"
    assert "token_hash" not in tokens[0]
    assert "raw_token" not in tokens[0]
    assert tokens[0]["revoked_at"] is None


def test_revoke_api_token_is_scoped_to_its_owner(conn):
    user1 = core.add_user(conn, "coach1@example.com", "correct-horse")
    user2 = core.add_user(conn, "coach2@example.com", "correct-horse")
    token_id, raw_token = core.create_api_token(conn, user1, "My Laptop")

    core.revoke_api_token(conn, token_id, user2)  # wrong owner -- no-op
    assert core.verify_api_token(conn, raw_token) is not None

    core.revoke_api_token(conn, token_id, user1)
    assert core.verify_api_token(conn, raw_token) is None
