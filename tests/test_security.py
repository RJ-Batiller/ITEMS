import pytest


@pytest.fixture()
def client():
    from app import app

    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_security_headers_are_present(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Permissions-Policy"] == "camera=(self), microphone=(), geolocation=()"
    assert "Content-Security-Policy" in response.headers


def test_hsts_is_only_sent_for_https_requests(client):
    response = client.get("/login", base_url="https://localhost")

    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]


def test_post_without_csrf_token_is_rejected(client):
    response = client.post("/login", data={"username": "test", "password": "test"})

    assert response.status_code == 400
    assert b"CSRF token" in response.data


def test_login_form_contains_csrf_token(client):
    response = client.get("/login")

    assert b'name="csrf_token"' in response.data


def test_unknown_page_uses_safe_error_view(client):
    response = client.get("/page-that-does-not-exist")

    assert response.status_code == 404
    assert b"Page not found" in response.data
    assert b"Traceback" not in response.data


def test_status_changes_reject_unknown_status():
    from app import validate_status_change

    assert validate_status_change("Available", "Unknown") is False
    assert validate_status_change("Disposed", "Available") is False
    assert validate_status_change("Available", "Under Maintenance") is False


def test_email_validation_rejects_malformed_values():
    from app import optional_email

    assert optional_email("admin@example.com") == "admin@example.com"
    with pytest.raises(ValueError):
        optional_email("not-an-email")


def test_staff_feature_permissions_override_role_defaults():
    from app import can_perform_action

    assert can_perform_action("Staff", "edit", {"edit": False}) is False
    assert can_perform_action("Staff", "edit", {"edit": True}) is True
    assert can_perform_action("Staff", "edit", {"edit": True, "view_all_equipment": True}) is True
    assert can_perform_action("Staff", "view", {"view_all_equipment": True}) is True
    assert can_perform_action("Staff", "view_all_equipment", {}) is False
    assert can_perform_action("Admin", "view_all_equipment", {}) is True
