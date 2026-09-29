"""Auth middleware: every /api/* call needs a valid bearer token; pairing codes are
single-use and expire."""

from __future__ import annotations


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_api_requires_token(client):
    r = client.get("/api/agents")
    assert r.status_code == 401


def test_api_rejects_wrong_token(client):
    r = client.get("/api/agents", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_api_accepts_valid_token(client, auth_headers):
    r = client.get("/api/agents", headers=auth_headers)
    assert r.status_code == 200


def test_pairing_code_is_single_use(client, auth_headers):
    new = client.post("/api/pair/new", headers=auth_headers, json={})
    assert new.status_code == 200
    code = new.json()["code"]

    first = client.post("/api/pair", json={"code": code})
    assert first.status_code == 200
    assert "token" in first.json()

    second = client.post("/api/pair", json={"code": code})
    assert second.status_code == 401


def test_pairing_code_wrong_is_rejected(client):
    r = client.post("/api/pair", json={"code": "AAAA-BBBB-CCCC"})
    assert r.status_code == 401


def test_pair_with_token_works_without_code(client, token):
    r = client.post("/api/pair", json={"token": token})
    assert r.status_code == 200
    assert r.json()["token"] == token
