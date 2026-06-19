from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "guardianci_false_positive.py"
SPEC = importlib.util.spec_from_file_location("guardianci_false_positive", SCRIPT_PATH)
fp = importlib.util.module_from_spec(SPEC)
sys.modules["guardianci_false_positive"] = fp
SPEC.loader.exec_module(fp)


def test_false_positive_command_detection() -> None:
    assert fp.is_false_positive_command("/fp")
    assert fp.is_false_positive_command("/FP")
    assert fp.is_false_positive_command("/fp this is safe here")
    assert not fp.is_false_positive_command("/fix")
    assert not fp.is_false_positive_command("please /fp")


def test_exclusion_record_extracts_guardianci_inline_comment() -> None:
    line = 'api_key = "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAA"'
    target = {
        "id": 12345,
        "path": "app/api/demo.py",
        "line": 7,
        "diff_hunk": f"""@@ -4,2 +6,3 @@
 def endpoint():
+{line}
     return True""",
        "body": (
            "**GuardianCI CRITICAL**\n\n"
            "Possible hardcoded secret or API key added in this change.\n\n"
            "Suggested fix: Move the value into a GitHub secret or environment variable."
        ),
    }
    context = {"sender": "noobbatman", "pr_number": 42}

    record = fp.exclusion_record(context, target)

    assert record is not None
    assert record["file_pattern"] == "app/api/demo.py"
    assert record["issue_type"] == "Possible hardcoded secret or API key added in this change."
    assert record["code_context_hash"] == fp.code_context_hash(line)
    assert record["dismissed_by"] == "noobbatman"
    assert record["source_pr_number"] == 42


def test_exclusion_record_requires_target_code_context() -> None:
    target = {
        "id": 12345,
        "path": "app/api/demo.py",
        "line": 7,
        "diff_hunk": "@@ -1,1 +1,1 @@\n def endpoint():",
        "body": (
            "**GuardianCI CRITICAL**\n\n"
            "Possible hardcoded secret or API key added in this change.\n\n"
            "Suggested fix: Move the value into a GitHub secret or environment variable."
        ),
    }
    context = {"sender": "noobbatman", "pr_number": 42}

    assert fp.exclusion_record(context, target) is None


def test_render_audit_body_lists_active_exclusions() -> None:
    body = fp.render_audit_body(
        [
            {
                "file_pattern": "app/api.py",
                "issue_type": "TLS certificate verification is disabled.",
                "dismissed_by": "noobbatman",
            }
        ]
    )

    assert "Active exclusions: 1" in body
    assert "`app/api.py`" in body
    assert "TLS certificate verification" in body


def test_render_audit_body_with_no_exclusions() -> None:
    body = fp.render_audit_body([])

    assert "no active exclusions" in body


def test_is_guardianci_finding_body_requires_both_markers() -> None:
    assert fp.is_guardianci_finding_body("**GuardianCI CRITICAL**\n\nSuggested fix: use env.") is True
    assert fp.is_guardianci_finding_body("**GuardianCI WARN**\n\nSuggested fix: ...") is True
    assert fp.is_guardianci_finding_body("**GuardianCI CRITICAL**\n\nNo fix marker here.") is False
    assert fp.is_guardianci_finding_body("Suggested fix: use env.") is False
    assert fp.is_guardianci_finding_body("") is False


def test_extract_issue_type_strips_severity_prefix_and_fix_suffix() -> None:
    body = (
        "**GuardianCI CRITICAL**\n\n"
        "Possible hardcoded secret or API key added in this change.\n\n"
        "Suggested fix: Move the value into a GitHub secret."
    )

    issue_type = fp.extract_issue_type(body)

    assert issue_type == "Possible hardcoded secret or API key added in this change."
    assert "GuardianCI" not in issue_type
    assert "Suggested fix" not in issue_type


def test_parse_comment_id_finds_numeric_id_in_fp_command() -> None:
    assert fp.parse_comment_id("/fp 1234567") == 1234567
    assert fp.parse_comment_id("/fp comment-id: 9876543") == 9876543
    assert fp.parse_comment_id("/fp") is None
    assert fp.parse_comment_id("/fp abc") is None


def test_same_exclusion_matches_on_all_three_key_fields() -> None:
    base = {
        "file_pattern": "app/api.py",
        "issue_type": "TLS verification disabled.",
        "code_context_hash": "abc123",
    }

    assert fp.same_exclusion(base, base) is True
    assert fp.same_exclusion(base, {**base, "file_pattern": "app/other.py"}) is False
    assert fp.same_exclusion(base, {**base, "issue_type": "Different issue."}) is False
    assert fp.same_exclusion(base, {**base, "code_context_hash": "xyz789"}) is False


def test_load_local_exclusions_accepts_bare_list_format(tmp_path, monkeypatch) -> None:
    exclusions_file = tmp_path / "exclusions.json"
    exclusions_file.write_text(
        '[{"file_pattern": "app/api.py", "issue_type": "TLS", "code_context_hash": "abc"}]',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    payload = fp.load_local_exclusions()

    assert isinstance(payload["exclusions"], list)
    assert payload["exclusions"][0]["file_pattern"] == "app/api.py"


def test_load_local_exclusions_returns_empty_dict_for_missing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    payload = fp.load_local_exclusions()

    assert payload == {"schema_version": 1, "exclusions": []}
