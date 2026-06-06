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
