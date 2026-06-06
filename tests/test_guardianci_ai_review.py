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
    assert review.is_reviewable_path("tests/test_api.py") is False
    assert review.is_reviewable_path("docs/security.md") is False
    assert review.is_reviewable_path("scripts/guardianci_ai_review.py") is False
    assert review.is_reviewable_path("app/api/routes.py") is True


def test_select_review_patches_skips_tests_and_prioritizes_high_risk_paths() -> None:
    selected = review.select_review_patches(
        [
            ("tests/test_api.py", "test patch"),
            ("frontend/app.tsx", "frontend patch"),
            ("app/services/payments.py", "service patch"),
            ("app/api/routes.py", "api patch"),
        ]
    )

    assert selected == [
        ("app/api/routes.py", "api patch"),
        ("app/services/payments.py", "service patch"),
        ("frontend/app.tsx", "frontend patch"),
    ]


def test_truthy_parses_common_enabled_values() -> None:
    assert review.truthy("true") is True
    assert review.truthy("1") is True
    assert review.truthy("false") is False
    assert review.truthy(None) is False


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


def test_parse_hunk_lines_marks_added_and_context_lines_once() -> None:
    patch = """diff --git a/app/demo.py b/app/demo.py
--- a/app/demo.py
+++ b/app/demo.py
@@ -10,2 +10,3 @@ def endpoint():
 context = True
-raw_sql = "SELECT * FROM users"
+raw_sql = f"SELECT * FROM users WHERE id = {user_id}"
+return raw_sql
 unchanged = True
"""

    parsed = review.parse_hunk_lines(patch)

    assert parsed == [
        (10, "context = True", False),
        (11, 'raw_sql = f"SELECT * FROM users WHERE id = {user_id}"', True),
        (12, "return raw_sql", True),
        (13, "unchanged = True", False),
    ]


def test_parse_hunk_lines_preserves_content_that_looks_like_headers() -> None:
    patch = """diff --git a/app/demo.py b/app/demo.py
--- a/app/demo.py
+++ b/app/demo.py
@@ -1,4 +1,5 @@
 first = True
---- removed delimiter
++++ added delimiter
+second = True
\\ No newline at end of file
 third = True
"""

    parsed = review.parse_hunk_lines(patch)

    assert parsed == [
        (1, "first = True", False),
        (2, "+++ added delimiter", True),
        (3, "second = True", True),
        (4, "third = True", False),
    ]


def test_collect_diff_uses_five_lines_of_context(monkeypatch) -> None:
    calls = []

    class FakeResult:
        stdout = "diff output"

    def fake_run(args, **_kwargs):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(review.subprocess, "run", fake_run)

    assert review.collect_diff("main") == "diff output"
    assert calls[1] == [
        "git",
        "diff",
        "--unified=5",
        "--diff-filter=ACMRT",
        "origin/main...HEAD",
    ]


def test_local_security_findings_detects_obvious_critical_patterns() -> None:
    patch = """diff --git a/app/api/demo.py b/app/api/demo.py
--- a/app/api/demo.py
+++ b/app/api/demo.py
@@ -1,2 +1,8 @@
 def endpoint(user_id):
+    api_key = "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
+    raw_sql = f"SELECT * FROM users WHERE id = {user_id}"
+    jwt_options = {"alg": "none"}
+    logger.info("token %s", token)
+    response = requests.get("https://example.test", verify=False)
     return True
"""

    findings = review.local_security_findings([("app/api/demo.py", patch)])

    severities = [finding.severity for finding in findings]
    issues = " ".join(finding.issue for finding in findings)
    assert severities.count("CRITICAL") == 3
    assert severities.count("WARN") == 2
    assert "hardcoded secret" in issues
    assert "SQL injection" in issues
    assert all(finding.frameworks for finding in findings)
    assert findings[0].remediation_urgency == "before-merge"


def test_merge_findings_deduplicates_matching_items() -> None:
    finding = review.Finding(
        file="app/api.py",
        line_start=1,
        line_end=1,
        severity="WARN",
        issue="Repeated",
        suggested_fix="Fix once.",
    )

    assert review.merge_findings([finding], [finding]) == [finding]


def test_truncate_diff_stops_at_budget() -> None:
    text, truncated = review.truncate_diff(
        [("a.py", "x" * 10), ("b.py", "y" * 10)],
        max_chars=15,
    )

    assert text == "xxxxxxxxxx"
    assert truncated is True


def test_truncate_diff_keeps_first_patch_when_it_exceeds_budget() -> None:
    text, truncated = review.truncate_diff([("a.py", "x" * 20)], max_chars=8)

    assert text.startswith("xxxxxxxx")
    assert "GuardianCI truncated" in text
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
                "frameworks": ["SOC 2 CC6.1", "GDPR Art. 32"],
                "remediation_urgency": "before-merge",
            },
            {
                "file": "docs/readme.md",
                "line_start": 1,
                "line_end": 1,
                "severity": "WARN",
                "issue": "Outside diff.",
                "suggested_fix": "No-op.",
                "frameworks": ["SOC 2 CC7.2"],
                "remediation_urgency": "within-sprint",
            },
            {"file": "app/api.py", "severity": "BAD"},
        ]
    }

    findings, errors = review.validate_findings(payload, changed)

    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert findings[0].is_critical is True
    assert findings[0].frameworks == ("SOC 2 CC6.1", "GDPR Art. 32")
    assert findings[0].remediation_urgency == "before-merge"
    assert len(errors) == 2


def test_validate_findings_defaults_optional_compliance_fields() -> None:
    payload = {
        "findings": [
            {
                "file": "app/api.py",
                "line_start": 42,
                "severity": "WARN",
                "issue": "Missing tenant check.",
                "suggested_fix": "Derive tenant from the authenticated principal.",
            }
        ]
    }

    findings, errors = review.validate_findings(payload, {"app/api.py": {42}})

    assert errors == []
    assert len(findings) == 1
    assert findings[0].line_end == 42
    assert findings[0].frameworks == ()
    assert findings[0].remediation_urgency == "within-sprint"


def test_validate_findings_rejects_explicit_invalid_line_end() -> None:
    payload = {
        "findings": [
            {
                "file": "app/api.py",
                "line_start": 42,
                "line_end": 0,
                "severity": "WARN",
                "issue": "Missing tenant check.",
                "suggested_fix": "Derive tenant from the authenticated principal.",
            }
        ]
    }

    findings, errors = review.validate_findings(payload, {"app/api.py": {42}})

    assert findings == []
    assert any("invalid line range: 42-0" in error for error in errors)


def test_validate_findings_rejects_bad_phase_3_fields() -> None:
    payload = {
        "findings": [
            {
                "file": "app/api.py",
                "line_start": 42,
                "line_end": 42,
                "severity": "WARN",
                "issue": "TLS verification disabled.",
                "suggested_fix": "Remove verify=False.",
                "frameworks": "SOC 2 CC6.7",
                "remediation_urgency": "soon",
            }
        ]
    }

    findings, errors = review.validate_findings(payload, {"app/api.py": {42}})

    assert findings == []
    assert "frameworks must be a list" in errors[0]


def test_normalize_frameworks_skips_non_strings_and_deduplicates() -> None:
    frameworks = review.normalize_frameworks(
        [" SOC 2 CC6.1 ", None, "GDPR Art. 32", "SOC 2 CC6.1", "", 123]
    )

    assert frameworks == ("SOC 2 CC6.1", "GDPR Art. 32")


def test_inline_comments_only_include_changed_lines() -> None:
    findings = [
        review.Finding(
            file="app/api.py",
            line_start=42,
            line_end=42,
            severity="WARN",
            issue="PII is logged.",
            suggested_fix="Remove the log line.",
            frameworks=("SOC 2 CC7.2", "GDPR Art. 32"),
            remediation_urgency="within-sprint",
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
    assert "SOC 2 CC7.2" in comments[0]["body"]
    assert "within-sprint" in comments[0]["body"]


def test_review_body_mentions_critical_findings_and_validation_errors() -> None:
    findings = [
        review.Finding(
            file="app/api.py",
            line_start=42,
            line_end=42,
            severity="CRITICAL",
            issue="SQL injection.",
            suggested_fix="Use parameters.",
            frameworks=("PCI-DSS 6.2.4", "SOC 2 CC6.1", "GDPR Art. 32"),
            remediation_urgency="before-merge",
        )
    ]

    body = review.render_review_body(findings, ["bad shape"], truncated=True)

    assert "CRITICAL: 1" in body
    assert "Frameworks touched: GDPR Art. 32, PCI-DSS 6.2.4, SOC 2 CC6.1" in body
    assert "before-merge: 1" in body
    assert "block this PR" in body
    assert "diff was truncated" in body
    assert "bad shape" in body


def test_no_finding_body_is_non_blocking() -> None:
    body = review.render_review_body([], [], truncated=False)

    assert "no blocking security findings" in body


def test_review_body_can_describe_local_only_review() -> None:
    body = review.render_review_body([], [], truncated=False, gemini_ran=False)

    assert body.startswith("GuardianCI compliance review")
    assert "Gemini" not in body


def test_user_prompt_requires_compliance_schema_fields() -> None:
    prompt = review.user_prompt("diff --git a/app/api.py b/app/api.py", truncated=False)

    assert "PCI-DSS 4.0" in prompt
    assert '"frameworks"' in prompt
    assert '"remediation_urgency"' in prompt


def test_quota_error_detection_matches_common_provider_messages() -> None:
    assert review.is_quota_or_rate_limit_error(RuntimeError("429 TooManyRequests")) is True
    assert review.is_quota_or_rate_limit_error(RuntimeError("RESOURCE_EXHAUSTED quota")) is True
    assert review.is_quota_or_rate_limit_error(RuntimeError("network down")) is False


def test_validate_fix_payload_strips_code_fences() -> None:
    payload = {"replacement": "```python\nsafe_value = os.getenv('API_KEY')\n```"}

    assert review.validate_fix_payload(payload) == "safe_value = os.getenv('API_KEY')"


def test_validate_fix_payload_rejects_empty_code_inside_fences() -> None:
    payload = {"replacement": "```python\n\n```"}

    try:
        review.validate_fix_payload(payload)
    except ValueError as exc:
        assert "non-empty code inside" in str(exc)
    else:
        raise AssertionError("Expected empty fenced replacement to be rejected.")


def test_build_fix_context_uses_bounded_window_and_imports() -> None:
    file_text = "\n".join(
        [
            "import os",
            "SECRET = 'do-not-send-unrelated-context'",
            *[f"line_{idx} = {idx}" for idx in range(1, 16)],
        ]
    )
    finding = review.Finding(
        file="app/api.py",
        line_start=10,
        line_end=10,
        severity="CRITICAL",
        issue="Hardcoded secret.",
        suggested_fix="Use env.",
    )

    context = review.build_fix_context(file_text, finding, radius=2)

    assert "1: import os" in context
    assert "10: line_8 = 8" in context
    assert "12: line_10 = 10" in context
    assert "SECRET = 'do-not-send-unrelated-context'" not in context


def test_apply_line_replacement_replaces_exact_range(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("first = True\nbad = True\nlast = True\n", encoding="utf-8")

    review.apply_line_replacement(target, 2, 2, "good = True")

    assert target.read_text(encoding="utf-8") == "first = True\ngood = True\nlast = True\n"


def test_auto_fix_branch_name_is_stable_and_sanitized() -> None:
    branch = review.auto_fix_branch_name(
        "ABCDEF123456",
        "JWT code appears to allow or reference the `alg=none` bypass pattern.",
    )

    assert branch.startswith("guardianCI/fix-abcdef12-jwt-code-appears")
    assert "`" not in branch


def test_safe_summary_removes_control_characters_and_collapses_whitespace() -> None:
    assert review.safe_summary("SQL\n injection\t issue", 80) == "SQL injection issue"
    assert review.safe_summary("\n\t", 80) == "security finding"


def test_auto_fix_pr_body_lists_original_pr_findings_and_files() -> None:
    finding = review.Finding(
        file="app/api.py",
        line_start=10,
        line_end=10,
        severity="CRITICAL",
        issue="SQL injection.",
        suggested_fix="Use bind parameters.",
    )

    body = review.auto_fix_pr_body(
        {"pr_number": 7},
        [finding],
        ["app/api.py"],
        needs_human_review=True,
    )

    assert "original PR #7" in body
    assert "`app/api.py:10` SQL injection." in body
    assert "`needs-human-review`" in body


def test_create_draft_fix_pr_posts_expected_payload(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"html_url": "https://github.test/pr/10", "number": 10}

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    finding = review.Finding(
        file="app/api.py",
        line_start=10,
        line_end=10,
        severity="CRITICAL",
        issue="SQL injection.\nDo not put this on line two.",
        suggested_fix="Use bind parameters.",
    )
    monkeypatch.setattr(review.requests, "post", fake_post)

    pr_url, pr_number = review.create_draft_fix_pr(
        context={
            "token": "token",
            "repo": "owner/repo",
            "pr_number": 7,
            "head_ref": "feature-branch",
        },
        branch="guardianCI/fix-abc-sql-injection",
        findings=[finding],
        fixed_files=["app/api.py"],
        needs_human_review=False,
    )

    assert pr_url == "https://github.test/pr/10"
    assert pr_number == 10
    assert calls[0]["json"]["draft"] is True
    assert "\n" not in calls[0]["json"]["title"]
    assert calls[0]["json"]["base"] == "feature-branch"
    assert calls[0]["json"]["head"] == "guardianCI/fix-abc-sql-injection"


def test_has_git_changes_uses_porcelain_status(monkeypatch) -> None:
    calls = []

    class FakeResult:
        stdout = " M app/api.py\n"

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return FakeResult()

    monkeypatch.setattr(review.subprocess, "run", fake_run)

    assert review.has_git_changes(["app/api.py"]) is True
    assert calls[0]["args"] == ["git", "status", "--porcelain", "--", "app/api.py"]


def test_post_auto_fix_comment_links_fix_pr(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(review.requests, "post", fake_post)

    review.post_auto_fix_comment(
        {"token": "token", "repo": "owner/repo", "pr_number": 7},
        review.AutoFixResult(
            branch="guardianCI/fix-abc",
            pr_url="https://github.test/pr/10",
            fixed_files=("app/api.py",),
            needs_human_review=False,
        ),
    )

    assert calls[0]["url"].endswith("/repos/owner/repo/issues/7/comments")
    assert "https://github.test/pr/10" in calls[0]["json"]["body"]


def test_request_fix_pr_reviewer_posts_requested_reviewer(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(review.requests, "post", fake_post)

    review.request_fix_pr_reviewer(
        {"token": "token", "repo": "owner/repo"},
        pr_number=10,
        reviewer="noobbatman",
    )

    assert calls[0]["url"].endswith("/repos/owner/repo/pulls/10/requested_reviewers")
    assert calls[0]["json"] == {"reviewers": ["noobbatman"]}


def test_post_review_logs_and_retries_body_only_on_inline_comment_rejection(
    monkeypatch, capsys
) -> None:
    calls = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            return None

    def fake_post(_url, *, headers, json, timeout):
        calls.append({"headers": headers, "json": dict(json), "timeout": timeout})
        return FakeResponse(422 if len(calls) == 1 else 200)

    monkeypatch.setattr(review.requests, "post", fake_post)

    review.post_review(
        {"token": "token", "repo": "owner/repo", "pr_number": 12},
        body="body",
        event="COMMENT",
        comments=[{"path": "app/api.py", "line": 1, "side": "RIGHT", "body": "comment"}],
    )

    output = capsys.readouterr().out
    assert "GitHub rejected 1 inline comment(s)" in output
    assert "comments" in calls[0]["json"]
    assert "comments" not in calls[1]["json"]
