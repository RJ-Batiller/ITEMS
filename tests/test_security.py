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
    assert "Content-Security-Policy" in response.headers


def test_post_without_csrf_token_is_rejected(client):
    response = client.post("/login", data={"username": "test", "password": "test"})

    assert response.status_code == 400
    assert b"CSRF token" in response.data


def test_login_form_contains_csrf_token(client):
    response = client.get("/login")

    assert b'name="csrf_token"' in response.data


def test_status_changes_reject_unknown_status():
    from app import validate_status_change

    assert validate_status_change("Available", "Unknown") is False
    assert validate_status_change("Disposed", "Available") is False
    assert validate_status_change("Available", "Under Maintenance") is True
