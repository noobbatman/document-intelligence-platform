from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "guardianci_ai_review.py"
SPEC = importlib.util.spec_from_file_location("guardianci_ai_review", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
sys.modules["guardianci_ai_review"] = review
SPEC.loader.exec_module(review)


def test_relevant_path_filter_includes_code_config_and_env_files() -> None:
    assert review.is_relevant_path("app/api/routes.py") is True
    assert review.is_relevant_path("frontend/src/app.tsx") is True
    assert review.is_relevant_path(".github/workflows/ci.yml") is True
    assert review.is_relevant_path(".env.example") is True
    assert review.is_relevant_path("Dockerfile") is True
    assert review.is_relevant_path("docs/guide.md") is False


def test_split_patches_and_changed_lines_track_new_file_lines() -> None:
    diff = """diff --git a/app/demo.py b/app/demo.py
index 1111111..2222222 100644
--- a/app/demo.py
+++ b/app/demo.py
@@ -10,2 +10,3 @@ def endpoint():
 context = True
-raw_sql = "SELECT * FROM users"
+raw_sql = f"SELECT * FROM users WHERE id = {user_id}"
+return raw_sql
 unchanged = True
diff --git a/docs/readme.md b/docs/readme.md
--- a/docs/readme.md
+++ b/docs/readme.md
@@ -1 +1 @@
-old
+new
"""

    patches = review.split_file_patches(diff)
    relevant = [(path, patch) for path, patch in patches if review.is_relevant_path(path)]
    changed = review.changed_new_lines(relevant)

    assert [path for path, _patch in patches] == ["app/demo.py", "docs/readme.md"]
    assert "app/demo.py" in changed
    assert changed["app/demo.py"] == {11, 12}


def test_truncate_diff_stops_at_budget() -> None:
    text, truncated = review.truncate_diff(
        [("a.py", "x" * 10), ("b.py", "y" * 10)],
        max_chars=15,
    )

    assert text == "xxxxxxxxxx"
    assert truncated is True


def test_parse_json_response_accepts_fences_and_embedded_objects() -> None:
    fenced = '```json\n{"findings": []}\n```'
    embedded = 'Gemini says:\n{"findings": [{"file": "app.py"}]}\nDone.'

    assert review.parse_json_response(fenced) == {"findings": []}
    assert review.parse_json_response(embedded)["findings"][0]["file"] == "app.py"


def test_validate_findings_accepts_good_items_and_reports_bad_items() -> None:
    changed = {"app/api.py": {42}}
    payload = {
        "findings": [
            {
                "file": "app/api.py",
                "line_start": 42,
                "line_end": 42,
                "severity": "critical",
                "issue": "Endpoint accepts tenant_id from the body without authorization.",
                "suggested_fix": "Derive tenant_id from the authenticated principal.",
            },
            {
                "file": "docs/readme.md",
                "line_start": 1,
                "line_end": 1,
                "severity": "WARN",
                "issue": "Outside diff.",
                "suggested_fix": "No-op.",
            },
            {"file": "app/api.py", "severity": "BAD"},
        ]
    }

    findings, errors = review.validate_findings(payload, changed)

    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert findings[0].is_critical is True
    assert len(errors) == 2


def test_inline_comments_only_include_changed_lines() -> None:
    findings = [
        review.Finding(
            file="app/api.py",
            line_start=42,
            line_end=42,
            severity="WARN",
            issue="PII is logged.",
            suggested_fix="Remove the log line.",
        ),
        review.Finding(
            file="app/api.py",
            line_start=99,
            line_end=99,
            severity="INFO",
            issue="Unchanged line.",
            suggested_fix="No-op.",
        ),
    ]

    comments = review.inline_comments(findings, {"app/api.py": {42}})

    assert len(comments) == 1
    assert comments[0]["line"] == 42
    assert "GuardianCI WARN" in comments[0]["body"]


def test_review_body_mentions_critical_findings_and_validation_errors() -> None:
    findings = [
        review.Finding(
            file="app/api.py",
            line_start=42,
            line_end=42,
            severity="CRITICAL",
            issue="SQL injection.",
            suggested_fix="Use parameters.",
        )
    ]

    body = review.render_review_body(findings, ["bad shape"], truncated=True)

    assert "CRITICAL: 1" in body
    assert "block this PR" in body
    assert "diff was truncated" in body
    assert "bad shape" in body


def test_no_finding_body_is_non_blocking() -> None:
    body = review.render_review_body([], [], truncated=False)

    assert "no blocking security findings" in body
