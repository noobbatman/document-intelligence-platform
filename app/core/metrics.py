"""Prometheus metrics registry — all metrics used in the platform."""

from prometheus_client import Counter, Gauge, Histogram


def tenant_label(tenant_id: str | None) -> str:
    return tenant_id or "public"


# ── Upload / processing ───────────────────────────────────────────────────────
documents_uploaded_total = Counter(
    "docintel_documents_uploaded_total",
    "Total documents uploaded.",
    ["tenant_id"],
)
documents_processed_total = Counter(
    "docintel_documents_processed_total",
    "Total documents processed.",
    ["status", "document_type", "tenant_id"],
)

# ── Review ────────────────────────────────────────────────────────────────────
review_tasks_total = Counter(
    "docintel_review_tasks_total",
    "Review tasks generated.",
    ["status"],
)
review_decisions_total = Counter(
    "docintel_review_decisions_total",
    "Review decisions submitted.",
    ["value_changed"],
)
corrections_recorded_total = Counter(
    "docintel_corrections_recorded_total",
    "Active-learning corrections captured.",
    ["document_type", "field_name"],
)

# ── Latency ───────────────────────────────────────────────────────────────────
pipeline_latency_seconds = Histogram(
    "docintel_pipeline_latency_seconds",
    "End-to-end document processing latency.",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
ocr_latency_seconds = Histogram(
    "docintel_ocr_latency_seconds",
    "OCR extraction latency.",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30),
)

# ── Confidence distribution ───────────────────────────────────────────────────
document_confidence_histogram = Histogram(
    "docintel_document_confidence",
    "Distribution of document-level confidence scores.",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95, 0.99),
)
ocr_confidence_histogram = Histogram(
    "docintel_ocr_confidence",
    "Distribution of OCR average confidence.",
    buckets=(0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99),
)

# ── Webhooks ──────────────────────────────────────────────────────────────────
webhooks_dispatched_total = Counter(
    "docintel_webhooks_dispatched_total",
    "Webhook dispatch attempts.",
    ["event", "success"],
)

# ── Queue depth ───────────────────────────────────────────────────────────────
queue_depth_gauge = Gauge(
    "docintel_queue_depth",
    "Pending documents in the processing queue.",
    ["queue"],
)

# ── HTTP ──────────────────────────────────────────────────────────────────────
http_requests_total = Counter(
    "docintel_http_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "docintel_http_request_duration_seconds",
    "HTTP request duration.",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ── Validation ────────────────────────────────────────────────────────────────
field_validation_failures_total = Counter(
    "docintel_field_validation_failures_total",
    "Field validation failures.",
    ["document_type", "field_name"],
)
