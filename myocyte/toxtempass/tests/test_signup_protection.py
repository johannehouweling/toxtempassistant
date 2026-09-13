"""Tests for the signup honeypot and the per-IP rate limits."""

import pytest
from django.urls import reverse

from toxtempass import Config, utilities
from toxtempass.models import Person

pytestmark = pytest.mark.django_db

SIGNUP_DATA = {
    "email": "real.person@example.org",
    "first_name": "Real",
    "last_name": "Person",
    "organization": "RIVM",
    "password1": "a-Long-and-unusual-pass-42",
    "password2": "a-Long-and-unusual-pass-42",
    "has_accepted_tos": "on",
}


def test_filled_in_honeypot_blocks_the_signup(client):
    response = client.post(
        reverse("signup"), {**SIGNUP_DATA, "website": "https://spam.example"}
    )

    assert response.json()["success"] is False
    assert not Person.objects.filter(email=SIGNUP_DATA["email"]).exists()


def test_honeypot_is_hidden_on_the_signup_page(client):
    content = client.get(reverse("signup")).content.decode()
    assert 'name="website"' in content
    assert '<div class="d-none" aria-hidden="true">' in content


def test_signup_is_rate_limited_per_ip(client):
    limit, _window = Config._ip_rate_limits["signup"]
    for attempt in range(limit):
        response = client.post(reverse("signup"), {"email": f"bot{attempt}"})
        assert response.status_code == 200

    blocked = client.post(reverse("signup"), SIGNUP_DATA)
    assert blocked.status_code == 429
    assert not Person.objects.filter(email=SIGNUP_DATA["email"]).exists()

    other_address = client.post(
        reverse("signup"), SIGNUP_DATA, HTTP_X_FORWARDED_FOR="203.0.113.7"
    )
    assert other_address.json()["success"] is True


def test_login_is_rate_limited_per_ip(client):
    limit, _window = Config._ip_rate_limits["login"]
    credentials = {"username": "someone@example.org", "password": "wrong"}
    for _ in range(limit):
        assert client.post(reverse("login"), credentials).status_code == 200

    assert client.post(reverse("login"), credentials).status_code == 429


def test_password_reset_is_rate_limited_per_ip(client):
    limit, _window = Config._ip_rate_limits["password_reset"]
    for _ in range(limit):
        client.post(reverse("password_reset"), {"email": "nobody@example.org"})

    response = client.post(reverse("password_reset"), {"email": "nobody@example.org"})
    assert response.status_code == 200
    assert Config._rate_limited_message in response.content.decode()


def test_client_ip_trusts_only_the_address_the_proxy_appended(rf):
    forwarded = rf.get(
        "/", HTTP_X_FORWARDED_FOR="10.0.0.1, 198.51.100.4", REMOTE_ADDR="172.18.0.2"
    )
    assert utilities.client_ip(forwarded) == "198.51.100.4"
    assert utilities.client_ip(rf.get("/", REMOTE_ADDR="172.18.0.2")) == "172.18.0.2"
