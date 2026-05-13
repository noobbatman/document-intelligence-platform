"""Tests for field validators and normalizers."""

from __future__ import annotations

from app.utils.validators import (
    parse_amount,
    parse_date,
    validate_account_number,
    validate_balance_consistency,
    validate_bank_statement_fields,
    validate_invoice_fields,
    validate_invoice_number,
    validate_invoice_total_consistency,
    validate_statement_period,
)


class TestDateParsing:
    def test_iso_format(self):
        assert parse_date("2024-01-15") == "2024-01-15"

    def test_dd_mm_yyyy(self):
        assert parse_date("15/01/2024") == "2024-01-15"

    def test_dd_month_yyyy(self):
        assert parse_date("15 January 2024") == "2024-01-15"

    def test_month_dd_yyyy(self):
        assert parse_date("January 15, 2024") == "2024-01-15"

    def test_abbrev_month(self):
        assert parse_date("15 Jan 2024") == "2024-01-15"

    def test_invalid_returns_none(self):
        assert parse_date("not a date") is None

    def test_none_input(self):
        assert parse_date(None) is None


class TestAmountParsing:
    def test_gbp_symbol(self):
        assert parse_amount("GBP 1,234.56") == 1234.56

    def test_dollar(self):
        assert parse_amount("$9,999.99") == 9999.99

    def test_plain_float(self):
        assert parse_amount(999.99) == 999.99

    def test_integer(self):
        assert parse_amount(100) == 100.0

    def test_with_commas(self):
        assert parse_amount("1,000.00") == 1000.0

    def test_none_returns_none(self):
        assert parse_amount(None) is None

    def test_invalid_returns_none(self):
        assert parse_amount("N/A") is None


class TestInvoiceValidators:
    def test_valid_invoice_number(self):
        _, ok, _ = validate_invoice_number("INV-2024-001")
        assert ok is True

    def test_invalid_invoice_number(self):
        _, ok, reason = validate_invoice_number("!INVALID!")
        assert ok is False

    def test_missing_invoice_number(self):
        _, ok, _ = validate_invoice_number(None)
        assert ok is False

    def test_total_consistency_pass(self):
        ok, _ = validate_invoice_total_consistency(100.0, 20.0, 120.0)
        assert ok is True

    def test_total_consistency_fail(self):
        ok, reason = validate_invoice_total_consistency(100.0, 20.0, 200.0)
        assert ok is False
        assert "error" in reason.lower()

    def test_total_consistency_skip_incomplete(self):
        ok, reason = validate_invoice_total_consistency(None, 20.0, 120.0)
        assert ok is True  # can't check with missing field


class TestBankValidators:
    def test_valid_account_number(self):
        _, ok, _ = validate_account_number("1234-5678")
        assert ok is True

    def test_valid_iban(self):
        _, ok, reason = validate_account_number("GB29NWBK60161331926819")
        assert ok is True
        assert "IBAN" in reason

    def test_invalid_account(self):
        _, ok, _ = validate_account_number("??")
        assert ok is False

    def test_valid_period(self):
        _, ok, _ = validate_statement_period("01 January 2024 - 31 January 2024")
        assert ok is True

    def test_period_with_to(self):
        _, ok, _ = validate_statement_period("01/01/2024 to 31/01/2024")
        assert ok is True

    def test_invalid_period(self):
        _, ok, _ = validate_statement_period("just text no dates")
        assert ok is False

    def test_balance_consistency_pass(self):
        ok, _ = validate_balance_consistency(1000.0, 1200.0, 1200.0)
        assert ok is True

    def test_balance_discrepancy_fails(self):
        ok, reason = validate_balance_consistency(1000.0, 1200.0, 900.0)
        assert ok is False


class TestFullValidationRunners:
    def test_invoice_runner_returns_results(self):
        results = validate_invoice_fields(
            {
                "invoice_number": "INV-001",
                "invoice_date": "2024-01-15",
                "subtotal": 100.0,
                "tax": 20.0,
                "total_amount": 120.0,
            }
        )
        assert any(r["field"] == "invoice_number" for r in results)
        assert any(r["field"] == "_cross_total" for r in results)

    def test_bank_runner_returns_results(self):
        results = validate_bank_statement_fields(
            {
                "account_number": "1234-5678",
                "statement_period": "01/01/2024 to 31/01/2024",
                "opening_balance": 1000.0,
                "closing_balance": 1200.0,
                "available_balance": 1200.0,
            }
        )
        assert any(r["field"] == "account_number" for r in results)
        assert any(r["field"] == "_cross_balance" for r in results)
