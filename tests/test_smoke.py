import pytest


@pytest.fixture()
def client():
    from app import app

    app.config.update(TESTING=True)

    with app.test_client() as test_client:
        yield test_client


def test_home_redirects_to_login(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_login_page_loads(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert b"ITEMS" in response.data