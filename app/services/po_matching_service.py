"""Purchase Order (PO) matching service.

Three-way match: Invoice ↔ Purchase Order ↔ (optional) Goods Receipt.

Model definitions live in app.db.models (Fix: removed duplicate Base-derived
classes that previously shadowed the Alembic-managed schema).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, POMatch, PurchaseOrder


def _normalize_vendor(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(
        r"\b(?:ltd|limited|inc|corp|corporation|llc|plc|gmbh|srl|sarl|bv)\b", "", name, flags=re.I
    )
    return re.sub(r"\s+", " ", s).strip().lower()


def _amount_match(a: float | None, b: float | None, tolerance: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= tolerance


class POMatchingService:
    """Match invoices against registered purchase orders."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def register_po(
        self,
        po_number: str,
        vendor_name: str,
        total_amount: float | None = None,
        currency: str = "GBP",
        line_items: list | None = None,
        tenant_id: str | None = None,
    ) -> PurchaseOrder:
        po = PurchaseOrder(
            po_number=po_number,
            vendor_name=vendor_name,
            total_amount=total_amount,
            currency=currency,
            line_items=line_items or [],
            tenant_id=tenant_id,
        )
        self.db.add(po)
        self.db.commit()
        self.db.refresh(po)
        return po

    def list_pos(self, tenant_id: str | None = None) -> list[PurchaseOrder]:
        stmt = select(PurchaseOrder)
        if tenant_id:
            stmt = stmt.where(PurchaseOrder.tenant_id == tenant_id)
        return list(self.db.scalars(stmt.order_by(PurchaseOrder.created_at.desc())))

    def match(self, document: Document) -> POMatch:
        result = document.extraction_result
        if not result:
            return self._save_match(document.id, None, "unmatched", 0.0, [], {})

        fields = result.export_payload.get("fields", {})
        inv_vendor = fields.get("vendor_name")
        inv_total = fields.get("total_amount")
        inv_po_ref = fields.get("purchase_order")
        inv_currency = fields.get("currency", "GBP")

        candidates: list[PurchaseOrder] = []
        if inv_po_ref:
            exact = list(
                self.db.scalars(
                    select(PurchaseOrder).where(PurchaseOrder.po_number == str(inv_po_ref))
                )
            )
            candidates.extend(exact)

        norm_vendor = _normalize_vendor(inv_vendor)
        if norm_vendor:
            all_pos = list(self.db.scalars(select(PurchaseOrder)))
            for po in all_pos:
                if po not in candidates:
                    if (
                        norm_vendor in _normalize_vendor(po.vendor_name)
                        or _normalize_vendor(po.vendor_name) in norm_vendor
                    ):
                        candidates.append(po)

        if not candidates:
            return self._save_match(
                document.id,
                None,
                "unmatched",
                0.0,
                [{"field": "po", "issue": "No matching PO found for this vendor"}],
                {},
            )

        best_po, best_score = None, 0.0
        best_discrepancies: list[dict] = []
        best_matched: dict = {}

        for po in candidates:
            score, discrepancies, matched = self._score_match(
                po=po,
                inv_vendor=inv_vendor,
                inv_total=float(inv_total) if inv_total else None,
                inv_po_ref=inv_po_ref,
                inv_currency=inv_currency,
                inv_line_items=result.export_payload.get("line_items", []),
            )
            if score > best_score:
                best_score = score
                best_po = po
                best_discrepancies = discrepancies
                best_matched = matched

        status = (
            "matched" if best_score >= 0.85 else "partial" if best_score >= 0.50 else "unmatched"
        )
        return self._save_match(
            document.id,
            best_po.id if best_po else None,
            status,
            best_score,
            best_discrepancies,
            best_matched,
        )

    def get_match(self, document_id: str) -> POMatch | None:
        return self.db.scalar(select(POMatch).where(POMatch.document_id == document_id))

    def _score_match(
        self,
        po: PurchaseOrder,
        inv_vendor: str | None,
        inv_total: float | None,
        inv_po_ref: str | None,
        inv_currency: str,
        inv_line_items: list,
    ) -> tuple[float, list[dict], dict]:
        score = 0.0
        discrepancies: list[dict] = []
        matched: dict = {}

        if inv_po_ref and inv_po_ref == po.po_number:
            score += 0.40
            matched["po_number"] = po.po_number
        elif inv_po_ref:
            discrepancies.append(
                {
                    "field": "purchase_order",
                    "invoice": inv_po_ref,
                    "po": po.po_number,
                    "issue": "PO number mismatch",
                }
            )

        inv_norm = _normalize_vendor(inv_vendor)
        po_norm = _normalize_vendor(po.vendor_name)
        if inv_norm and po_norm and (inv_norm in po_norm or po_norm in inv_norm):
            score += 0.30
            matched["vendor_name"] = po.vendor_name
        elif inv_vendor:
            discrepancies.append(
                {
                    "field": "vendor_name",
                    "invoice": inv_vendor,
                    "po": po.vendor_name,
                    "issue": "Vendor name mismatch",
                }
            )

        if _amount_match(inv_total, po.total_amount):
            score += 0.30
            matched["total_amount"] = po.total_amount
        elif inv_total is not None and po.total_amount is not None:
            pct_diff = abs(inv_total - po.total_amount) / max(po.total_amount, 1) * 100
            discrepancies.append(
                {
                    "field": "total_amount",
                    "invoice": inv_total,
                    "po": po.total_amount,
                    "issue": f"Amount differs by {pct_diff:.1f}%",
                }
            )

        return score, discrepancies, matched

    def _save_match(
        self,
        document_id: str,
        po_id: str | None,
        status: str,
        score: float,
        discrepancies: list,
        matched: dict,
    ) -> POMatch:
        existing = self.get_match(document_id)
        if existing:
            self.db.delete(existing)
            self.db.flush()

        match = POMatch(
            document_id=document_id,
            po_id=po_id,
            match_status=status,
            match_score=round(score, 3),
            discrepancies=discrepancies,
            matched_fields=matched,
        )
        self.db.add(match)
        self.db.commit()
        self.db.refresh(match)
        return match
