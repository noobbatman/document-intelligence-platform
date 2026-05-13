"""Integration tests for webhook registration and management."""

from __future__ import annotations

BASE = "/api/v1/webhooks"


def test_register_webhook(client) -> None:
    response = client.post(
        BASE,
        json={
            "name": "My Hook",
            "url": "https://example.com/hook",
            "event": "processing_completed",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "My Hook"
    assert data["event"] == "processing_completed"
    assert data["status"] == "active"
    assert data["failure_count"] == 0


def test_register_webhook_with_secret(client) -> None:
    response = client.post(
        BASE,
        json={
            "name": "Secure Hook",
            "url": "https://example.com/secure",
            "event": "processing_failed",
            "secret": "s3cr3t",
        },
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Secure Hook"


def test_register_webhook_invalid_event(client) -> None:
    response = client.post(
        BASE,
        json={
            "name": "Bad Hook",
            "url": "https://example.com/hook",
            "event": "NOT_A_REAL_EVENT",
        },
    )
    assert response.status_code == 422


def test_list_webhooks(client) -> None:
    client.post(
        BASE, json={"name": "H1", "url": "https://a.com/1", "event": "processing_completed"}
    )
    client.post(BASE, json={"name": "H2", "url": "https://a.com/2", "event": "review_required"})
    response = client.get(BASE)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_deactivate_webhook(client) -> None:
    reg = client.post(
        BASE, json={"name": "H", "url": "https://a.com/h", "event": "processing_completed"}
    )
    wid = reg.json()["id"]
    del_resp = client.delete(f"{BASE}/{wid}")
    assert del_resp.status_code == 204
    hooks = client.get(BASE).json()
    assert all(h["status"] == "inactive" for h in hooks if h["id"] == wid)


def test_deactivate_nonexistent_webhook(client) -> None:
    response = client.delete(f"{BASE}/nonexistent-id")
    assert response.status_code == 404
