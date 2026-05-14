"""Tests for HTTP middleware: rate limiting and Prometheus metrics."""

from __future__ import annotations

import app.core.http_runtime as http_runtime
from app.core.config import get_settings
from app.core.metrics import http_request_duration_seconds, http_requests_total


def _sample_value(metric, sample_names: set[str], labels: dict[str, str]) -> float:
    for family in metric.collect():
        for sample in family.samples:
            if sample.name not in sample_names:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


def test_default_rate_limit_enforced(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_default_per_minute", 1)

    http_runtime.rate_limiter.reset()

    first = client.get("/api/v1/documents", headers={"X-Tenant-ID": "tenant-a"})
    second = client.get("/api/v1/documents", headers={"X-Tenant-ID": "tenant-a"})

    http_runtime.rate_limiter.reset()

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"
    assert first.headers["X-RateLimit-Remaining"] == "0"
    assert second.status_code == 429
    assert second.headers["Retry-After"]


def test_http_metrics_recorded_for_requests(client) -> None:
    counter_labels = {"method": "GET", "path": "/api/v1/health", "status": "200"}
    histogram_labels = {"method": "GET", "path": "/api/v1/health"}

    before_count = _sample_value(
        http_requests_total,
        {"docintel_http_requests_total", "docintel_http_requests_total_total"},
        counter_labels,
    )
    before_hist_count = _sample_value(
        http_request_duration_seconds,
        {"docintel_http_request_duration_seconds_count"},
        histogram_labels,
    )

    response = client.get("/api/v1/health")

    after_count = _sample_value(
        http_requests_total,
        {"docintel_http_requests_total", "docintel_http_requests_total_total"},
        counter_labels,
    )
    after_hist_count = _sample_value(
        http_request_duration_seconds,
        {"docintel_http_request_duration_seconds_count"},
        histogram_labels,
    )

    assert response.status_code == 200
    assert after_count == before_count + 1
    assert after_hist_count == before_hist_count + 1


def test_rate_limit_header_present_on_normal_request(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_default_per_minute", 120)

    http_runtime.rate_limiter.reset()
    r = client.get("/api/v1/documents")
    http_runtime.rate_limiter.reset()

    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers
