import pytest

import app as app_module
from app import app, can_complete_maintenance, can_perform_action, can_start_maintenance, creatable_roles, manageable_roles
from app import validate_status_change


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_super_admin_can_create_admin_and_worker():
    assert creatable_roles("Super Admin") == ("Admin", "Worker")


def test_admin_can_create_worker_only():
    assert creatable_roles("Admin") == ("Worker",)


def test_worker_cannot_create_users():
    assert creatable_roles("Worker") == ()


def test_management_scope_matches_role_hierarchy():
    assert manageable_roles("Super Admin") == ("Admin", "Worker")
    assert manageable_roles("Admin") == ("Worker",)
    assert manageable_roles("Worker") == ()


def test_worker_is_read_only_for_equipment():
    assert can_perform_action("Worker", "view") is True
    assert can_perform_action("Worker", "view_transactions") is True
    for action in ("add", "edit", "archive", "restore", "dispose", "assign", "unassign"):
        assert can_perform_action("Worker", action) is False


def test_admin_can_write_equipment_and_manage_users():
    assert can_perform_action("Admin", "edit") is True
    assert can_perform_action("Admin", "manage_users") is True


def test_super_admin_can_write_equipment_and_manage_users():
    assert can_perform_action("Super Admin", "maintenance") is True
    assert can_perform_action("Super Admin", "manage_users") is True


def test_maintenance_status_rules():
    assert can_start_maintenance("Available") is True
    assert can_start_maintenance("Under Maintenance") is True
    assert can_start_maintenance("Assigned") is False
    assert can_start_maintenance("Archived") is False
    assert can_start_maintenance("Disposed") is False
    assert can_complete_maintenance(True) is True
    assert can_complete_maintenance(False) is False
    assert validate_status_change("Available", "Under Maintenance") is False
    assert validate_status_change("Under Maintenance", "Assigned") is False
    assert validate_status_change("Under Maintenance", "Available") is False


def test_worker_is_blocked_from_user_management(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "get_current_user",
        lambda: {"role_name": "Worker", "full_name": "Worker", "is_active": 1},
    )
    with client.session_transaction() as session:
        session["user_id"] = 3

    response = client.get("/users")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


@pytest.mark.parametrize("path", ["/users/1/reset-password", "/users/1/toggle-status"])
def test_worker_is_blocked_from_super_admin_account_controls(client, monkeypatch, path):
    monkeypatch.setattr(
        app_module,
        "get_current_user",
        lambda: {"id": 3, "role_name": "Worker", "full_name": "Worker", "is_active": 1},
    )
    with client.session_transaction() as session:
        session["user_id"] = 3
        session["csrf_token"] = "test-token"

    response = client.post(path, data={"csrf_token": "test-token", "new_password": "new-password"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_signed_in_user_can_open_profile(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "get_current_user",
        lambda: {"id": 3, "username": "worker", "role_name": "Worker", "full_name": "Worker", "email": "worker@example.com", "is_active": 1},
    )
    with client.session_transaction() as session:
        session["user_id"] = 3

    response = client.get("/profile")

    assert response.status_code == 200
    assert b"My Profile" in response.data


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/equipment/edit/1"),
        ("post", "/equipment/add"),
        ("post", "/equipment/archive/1"),
        ("get", "/equipment/dispose/1"),
        ("get", "/equipment/assign/1"),
        ("post", "/equipment/unassign/1"),
    ],
)
def test_worker_is_blocked_from_equipment_actions(client, monkeypatch, method, path):
    monkeypatch.setattr(
        app_module,
        "get_current_user",
        lambda: {"role_name": "Worker", "full_name": "Worker", "is_active": 1},
    )
    with client.session_transaction() as session:
        session["user_id"] = 3
        session["csrf_token"] = "test-token"

    request_method = getattr(client, method)
    data = {"csrf_token": "test-token"} if method == "post" else None
    response = request_method(path, data=data)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/equipment")
