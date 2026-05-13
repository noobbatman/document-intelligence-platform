"""Duplicate and fraud detection service.

Checks performed:
  1. Content hash — exact byte-for-byte duplicate file
  2. Invoice number + vendor collision — same invoice already processed
  3. Amount anomaly — invoice total deviates >3σ from vendor historical mean
  4. Velocity check — same vendor submitted >N invoices in last 24 hours
"""

from __future__ import annotations

import hashlib
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus, ExtractionResult


def _sha256(path: str) -> str | None:
    """Return SHA-256 hex digest of a local file, or None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


class DeduplicationService:
    """Run duplicate + fraud checks before or after pipeline processing."""

    VELOCITY_WINDOW_HOURS = 24
    VELOCITY_MAX_PER_VENDOR = 20  # flag if vendor submits >20 invoices/day
    ANOMALY_MIN_HISTORY = 5  # need at least 5 past invoices to flag anomaly
    ANOMALY_SIGMA_THRESHOLD = 3.0  # flag if amount > mean + 3*stdev

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self, document: Document) -> dict[str, Any]:
        """Run all checks. Returns a risk report dict."""
        findings: list[dict] = []
        risk_score = 0.0
        stored_path = document.stored_path

        # 1. Content hash check
        if not stored_path.startswith("s3://"):
            sha = _sha256(stored_path)
            if sha:
                dup = self._find_by_hash(sha, exclude_id=document.id)
                if dup:
                    findings.append(
                        {
                            "type": "exact_duplicate",
                            "severity": "high",
                            "detail": f"Byte-for-byte duplicate of document {dup.id} ({dup.filename})",
                            "duplicate_document_id": dup.id,
                        }
                    )
                    risk_score += 0.8

        # 2. Invoice number + vendor collision
        inv_dup = self._find_invoice_number_collision(document)
        if inv_dup:
            findings.append(
                {
                    "type": "invoice_number_collision",
                    "severity": "high",
                    "detail": f"Invoice number already exists in document {inv_dup.id}",
                    "duplicate_document_id": inv_dup.id,
                }
            )
            risk_score += 0.7

        # 3. Amount anomaly
        anomaly = self._check_amount_anomaly(document)
        if anomaly:
            findings.append(anomaly)
            risk_score += 0.4

        # 4. Velocity check
        velocity = self._check_vendor_velocity(document)
        if velocity:
            findings.append(velocity)
            risk_score += 0.3

        return {
            "document_id": document.id,
            "risk_score": round(min(risk_score, 1.0), 3),
            "risk_level": self._risk_level(risk_score),
            "findings": findings,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def store_hash(self, document: Document) -> str | None:
        """Compute and store content hash in document tags for future lookups."""
        if document.stored_path.startswith("s3://"):
            return None
        sha = _sha256(document.stored_path)
        if sha:
            tags = dict(document.tags or {})
            tags["content_sha256"] = sha
            document.tags = tags
            self.db.flush()
        return sha

    # ── Private checks ────────────────────────────────────────────────────────

    def _find_by_hash(self, sha: str, exclude_id: str) -> Document | None:
        stmt = select(Document).where(
            and_(
                Document.id != exclude_id,
                Document.status.notin_([DocumentStatus.failed]),
                Document.tags["content_sha256"].astext == sha,
            )
        )
        return self.db.scalar(stmt)

    def _find_invoice_number_collision(self, document: Document) -> Document | None:
        result = document.extraction_result
        if not result:
            return None
        inv_num = result.export_payload.get("fields", {}).get("invoice_number")
        if not inv_num:
            return None

        # Select only the payload column to avoid N+1 lazy-loads on extraction_result.
        # Vendor-agnostic: invoice numbers should be globally unique within a tenant.
        from sqlalchemy.orm import joinedload

        stmt = (
            select(Document)
            .options(joinedload(Document.extraction_result))
            .join(Document.extraction_result)
            .where(
                and_(
                    Document.id != document.id,
                    Document.status.notin_([DocumentStatus.failed]),
                )
            )
        )
        for candidate in self.db.scalars(stmt):
            candidate_num = (
                candidate.extraction_result.export_payload.get("fields", {}).get("invoice_number")
                if candidate.extraction_result
                else None
            )
            if str(candidate_num) == str(inv_num):
                return candidate
        return None

    def _check_amount_anomaly(self, document: Document) -> dict | None:
        result = document.extraction_result
        if not result:
            return None
        total = result.export_payload.get("fields", {}).get("total_amount")
        vendor = result.export_payload.get("fields", {}).get("vendor_name")
        if total is None or not vendor:
            return None
        try:
            current_total = float(total)
        except (TypeError, ValueError):
            return None

        vendor_prefix = vendor[:20].lower()

        # Select only the JSON payload column — avoids loading full ORM objects and
        # removes the .limit(100) that silently discarded legitimate vendor history
        # whenever there were more than 100 completed documents in the database.
        stmt = (
            select(ExtractionResult.export_payload)
            .join(Document, Document.id == ExtractionResult.document_id)
            .where(
                and_(
                    Document.id != document.id,
                    Document.status == DocumentStatus.completed,
                )
            )
        )
        historical = []
        for payload in self.db.scalars(stmt):
            try:
                payload_vendor = str((payload or {}).get("fields", {}).get("vendor_name", ""))
                if vendor_prefix not in payload_vendor.lower():
                    continue
                value = float((payload or {}).get("fields", {}).get("total_amount", 0) or 0)
                if value > 0:
                    historical.append(value)
            except (TypeError, ValueError):
                pass

        if len(historical) < self.ANOMALY_MIN_HISTORY:
            return None

        mean = statistics.mean(historical)
        stdev = statistics.stdev(historical) if len(historical) > 1 else 0
        if stdev == 0:
            return None

        z_score = abs(current_total - mean) / stdev
        if z_score > self.ANOMALY_SIGMA_THRESHOLD:
            return {
                "type": "amount_anomaly",
                "severity": "medium",
                "detail": (
                    f"Total {current_total:.2f} is {z_score:.1f}σ from vendor mean "
                    f"{mean:.2f} (±{stdev:.2f}, n={len(historical)})"
                ),
                "z_score": round(z_score, 2),
                "vendor_mean": round(mean, 2),
                "vendor_stdev": round(stdev, 2),
                "historical_count": len(historical),
            }
        return None

    def _check_vendor_velocity(self, document: Document) -> dict | None:
        result = document.extraction_result
        if not result:
            return None
        vendor = result.export_payload.get("fields", {}).get("vendor_name")
        if not vendor:
            return None

        vendor_prefix = vendor[:20].lower()
        cutoff = datetime.now(UTC) - timedelta(hours=self.VELOCITY_WINDOW_HOURS)

        # Select only the payload column to avoid N+1 lazy-loads when iterating candidates.
        stmt = (
            select(ExtractionResult.export_payload)
            .join(Document, Document.id == ExtractionResult.document_id)
            .where(
                and_(
                    Document.id != document.id,
                    Document.created_at >= cutoff,
                )
            )
        )
        count = sum(
            1
            for payload in self.db.scalars(stmt)
            if vendor_prefix
            in str((payload or {}).get("fields", {}).get("vendor_name", "")).lower()
        )

        if count >= self.VELOCITY_MAX_PER_VENDOR:
            return {
                "type": "vendor_velocity",
                "severity": "low",
                "detail": f"Vendor '{vendor[:40]}' submitted {count} invoices in last {self.VELOCITY_WINDOW_HOURS}h",
                "count": count,
                "window_hours": self.VELOCITY_WINDOW_HOURS,
            }
        return None

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "medium"
        if score > 0:
            return "low"
        return "clean"
