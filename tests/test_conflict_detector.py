"""Tests for the intra-document conflict detector."""

from __future__ import annotations

from app.rag.conflict_detector import (
    ConflictItem,
    detect_conflicts,
    format_conflicts_block,
)

# ── Governing-law conflicts ───────────────────────────────────────────────────


def test_governing_law_conflict_detected():
    chunks = [
        "This Agreement shall be governed by the laws of New York.",
        "Payment terms are net-30 from invoice date.",
        "All disputes shall be governed by the laws of Delaware.",
    ]
    conflicts = detect_conflicts(chunks)
    gov_law = [c for c in conflicts if c.conflict_type == "governing_law"]
    assert len(gov_law) == 1
    assert gov_law[0].severity == "high"
    assert "new york" in gov_law[0].description.lower()
    assert "delaware" in gov_law[0].description.lower()
    assert 0 in gov_law[0].chunk_indices
    assert 2 in gov_law[0].chunk_indices


def test_governing_law_single_jurisdiction_no_conflict():
    chunks = [
        "This Agreement shall be governed by the laws of California.",
        "Disputes shall be resolved under the laws of California.",
        "The parties submit to jurisdiction in California.",
    ]
    conflicts = detect_conflicts(chunks)
    gov_law = [c for c in conflicts if c.conflict_type == "governing_law"]
    assert gov_law == []


# ── Defined-term conflicts ────────────────────────────────────────────────────


def test_defined_term_conflict_detected():
    chunks = [
        '"Confidential Information" means any non-public information disclosed by either party.',
        "Standard payment clauses apply.",
        '"Confidential Information" means trade secrets and proprietary technical data only.',
    ]
    conflicts = detect_conflicts(chunks)
    dt = [c for c in conflicts if c.conflict_type == "defined_term"]
    assert len(dt) == 1
    assert "confidential information" in dt[0].description.lower()
    assert dt[0].severity == "medium"


def test_defined_term_consistent_no_conflict():
    chunks = [
        '"Licensee" means the entity identified in the Order Form.',
        '"Licensee" means the entity identified in the Order Form.',
    ]
    conflicts = detect_conflicts(chunks)
    dt = [c for c in conflicts if c.conflict_type == "defined_term"]
    assert dt == []


# ── Date-label conflicts ──────────────────────────────────────────────────────


def test_date_label_conflict_detected():
    chunks = [
        "Effective Date: January 1, 2024.",
        "This agreement covers the scope of services.",
        "Effective Date: March 15, 2024.",
    ]
    conflicts = detect_conflicts(chunks)
    date = [c for c in conflicts if c.conflict_type == "date"]
    assert len(date) == 1
    assert "January 1, 2024" in date[0].description or "2024" in date[0].description
    assert date[0].severity == "medium"


# ── Amount conflicts ──────────────────────────────────────────────────────────


def test_amount_conflict_detected():
    chunks = [
        "The monthly fee shall be $5,000 per month.",
        "Invoices are due within 30 days.",
        "Monthly fee: $5,500 as per Schedule A.",
    ]
    conflicts = detect_conflicts(chunks)
    amount = [c for c in conflicts if c.conflict_type == "amount"]
    assert len(amount) == 1
    assert "5000" in amount[0].description or "5500" in amount[0].description
    assert amount[0].severity == "high"


# ── Clean document ────────────────────────────────────────────────────────────


def test_no_conflicts_clean_chunks():
    chunks = [
        "This Agreement is governed by the laws of New York.",
        "The monthly fee is $2,500.",
        "Effective Date: June 1, 2024.",
    ]
    conflicts = detect_conflicts(chunks)
    assert conflicts == []


# ── Existing defined terms seed ──────────────────────────────────────────────


def test_defined_term_conflict_with_existing_terms():
    # Priority 3 extracted "Company" = "Acme Corp"; a chunk redefines it differently
    existing = {"Company": "Acme Corp"}
    chunks = [
        '"Company" means the entity registered under the laws of Delaware.',
    ]
    conflicts = detect_conflicts(chunks, defined_terms=existing)
    dt = [c for c in conflicts if c.conflict_type == "defined_term"]
    assert len(dt) == 1
    assert "company" in dt[0].description.lower()


# ── format_conflicts_block ────────────────────────────────────────────────────


def test_format_conflicts_block_empty():
    assert format_conflicts_block([]) == ""


def test_format_conflicts_block_renders_severity_and_type():
    items = [
        ConflictItem(
            conflict_type="governing_law",
            description='Conflicting: "new york", "delaware".',
            chunk_indices=[0, 2],
            severity="high",
            field="governing_law",
        ),
        ConflictItem(
            conflict_type="defined_term",
            description='"company" has conflicting definitions.',
            chunk_indices=[1],
            severity="medium",
            field=None,
        ),
    ]
    block = format_conflicts_block(items)
    assert "KNOWN CONFLICTS" in block
    assert "[HIGH]" in block
    assert "GOVERNING_LAW" in block
    assert "[MEDIUM]" in block
    assert "DEFINED_TERM" in block


# ── ConflictItem.to_dict ──────────────────────────────────────────────────────


def test_conflict_item_to_dict_round_trips():
    item = ConflictItem(
        conflict_type="amount",
        description="Inconsistent monthly fee.",
        chunk_indices=[3, 8],
        severity="high",
        field="amount",
    )
    d = item.to_dict()
    assert d["conflict_type"] == "amount"
    assert d["chunk_indices"] == [3, 8]
    assert d["severity"] == "high"
    assert d["field"] == "amount"
