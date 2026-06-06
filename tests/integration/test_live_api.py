"""Live-container integration checks for the GuardianCI Docker smoke job."""

from __future__ import annotations

import os

import requests

BASE_URL = os.getenv("DOCINTEL_BASE_URL", "http://localhost:8000").rstrip("/")


def test_live_api_liveness_probe() -> None:
    response = requests.get(f"{BASE_URL}/api/v1/health", timeout=5)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_live_api_openapi_schema() -> None:
    response = requests.get(f"{BASE_URL}/api/v1/openapi.json", timeout=5)

    assert response.status_code == 200
    assert response.json()["info"]["title"]
